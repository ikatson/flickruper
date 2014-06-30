#!/usr/bin/env python

"""Flickr CLI multi-threaded uploader, can resume uploads on failures.

Now that Flickr gives everyone 1TB of space, it's very handy to store all
your original photos there. This tool helps upload lots of files to Flickr at
once.
"""

import argparse
import functools
import logging
import os
import re
import sys
import threading

import flickrapi

API_KEY = '19f22b87fad6fdc0be6b2108332f681a'
API_SECRET = '7ca50772a642b783'
FS_ENC = sys.getfilesystemencoding()

log = logging.getLogger(__name__)


def unicode_path(path):
    """Ensure the filesystem path is unicode, decode if not."""
    if isinstance(path, unicode):
        return path
    try:
        return path.decode(FS_ENC)
    except UnicodeDecodeError:
        log.warning('Cannot decode path %s as %s', path, FS_ENC)
        return path


def force_utf8(string):
    """Encode the argument to utf-8, if its unicode."""
    if isinstance(string, unicode):
        return string.encode('utf-8')
    return string


def force_fs_encoding(string):
    """Encode the argument to filesystem encoding, if its unicode."""
    if isinstance(string, unicode):
        return string.encode(FS_ENC)
    return string


class Photo(object):
    """A flickr Photo."""

    def __init__(self, api, id, title, photoset=None, attrs=None):
        self.api = api
        self.id = id
        self.title = title
        self.photoset = photoset
        self.attrs = attrs

    @classmethod
    def from_element(cls, api, element, photoset=None):
        attrs = element.attrib
        return cls(
            api, attrs['id'], attrs['title'], photoset=photoset, attrs=attrs)

    def __repr__(self):
        descr = []
        if self.title:
            descr.append('Title: "%s"' % self.title)
        if self.photoset:
            descr.append('Photoset: "%s"' % self.photoset.title)
        result = '<Photo %s: %s>' % (', '.join(descr))
        if isinstance(result, unicode):
            return result.encode('utf-8')
        return result


class PhotoSet(object):
    """flickr PhotoSet."""

    def __init__(self, api, id, title, description=None, attrs=None):
        assert isinstance(api, flickrapi.FlickrAPI)
        self.api = api
        self.title = title
        self.id = id
        self.description = description
        self.attrs = attrs or {}
        self._all_photos = []

    @classmethod
    def from_element(cls, api, element):
        """Create a PhotoSet object from XML element from API."""
        title = element.find('title').text
        description = element.find('description').text
        attrs = element.attrib
        return cls(api, attrs['id'], title, description, attrs=attrs)

    @classmethod
    def create(cls, api, title, primary_photo_id):
        """Create a new PhotoSet in flickr."""
        log.info('Creating photoset "%s", primary photo id: %s',
                 title, primary_photo_id)
        pset = api.photosets_create(
            title=title, primary_photo_id=primary_photo_id)
        pset = pset.find('photoset')
        assert pset is not None, 'cannot find "photoset" element'
        return cls(api, pset.attrib['id'], title, attrs=pset.attrib)

    def _walk(self):
        return self.api.walk_set(photoset_id=self.id)

    def walk(self, refresh=False):
        """Cached walk function, returns all Photos in this photoset."""
        if self._all_photos and not refresh:
            return self._all_photos
        self._all_photos = []
        for photo in self._walk():
            self._all_photos.append(
                Photo.from_element(api=self.api, element=photo, photoset=self))
        return self._all_photos

    def has_photo(self, title=None, id=None, refresh=False):
        """Check if the PhotoSet has the photo by title or id."""
        assert title or id
        if title:
            assert id is None, 'Only one of title or id should be provided'
        if id:
            assert title is None, 'Only one of title or id should be provided'
        for photo in self.walk(refresh=refresh):
            if title is not None and photo.title == title:
                return True
            if id is not None and photo.id == id:
                return True

    def add_photo(self, photo_or_photo_id):
        """Add a photo to this photoset. Accepts photo id or a Photo object."""
        if isinstance(photo_or_photo_id, Photo):
            photo_id = photo_or_photo_id.id
        else:
            photo_id = photo_or_photo_id
        if self.has_photo(id=photo_id):
            return
        log.debug('Adding photo %s to photoset "%s"', photo_id, self.title)
        self.api.photosets_addPhoto(photoset_id=self.id, photo_id=photo_id)
        self._all_photos.append(Photo(self.api, photo_id, None))

    def __repr__(self):
        result = '<Photoset %s: %s>' % (self.id, self.title)
        if isinstance(result, unicode):
            return result.encode('utf-8')
        return result


class MultithreadedUploader(object):

    # What photo formats to upload.
    PHOTO_RE = re.compile('.*\.(jpg|jpeg|png|gif|tif|tiff)$', re.IGNORECASE)

    # If percent of upload errors is greater than this, the uploader is
    # aborted.
    MAX_ERROR_PERCENT = 2

    def __init__(self, dirname, setname=None, tags=None, threads=4,
                 is_public=False):
        """
        Args:
          dirname: a directory to upload the photos from
          setname: the name of the set to upload all photos to. If not provided,
            the basename of the dir will be used. A new set
            will be created only if a set with this name does not exist.
          tags: either a space-delimited string, or a list
          threads: how many concurrent uploads to start
          is_public: if True, all uploaded photos will be public.
        """
        self.dirname = unicode_path(dirname)
        if not setname:
            setname = os.path.basename(dirname)
        setname = unicode_path(setname)
        if not os.path.isdir(dirname):
            raise OSError('Directory "%s" does not exist', dirname)
        self.setname = setname.strip()
        if isinstance(tags, list):
            tags = ' '.join(tags)
        self.tags = tags
        self.is_public = is_public
        self._is_authenticated = False
        self._threadcount = threads
        self.flickr = flickrapi.FlickrAPI(API_KEY, API_SECRET)
        self._photoset = None
        self._all_photosets = []
        self._lock = threading.RLock()
        self._semaphore = threading.Semaphore(threads)
        self._errorcount = 0
        self._should_quit = threading.Event()
        self.authenticate()

    def run(self):
        """The main function to start the uploader."""

        threads = []
        photos_to_upload = self.get_photos_to_upload()
        errors_allowed = len(photos_to_upload) / 100.0 * self.MAX_ERROR_PERCENT

        try:
            for index, fname in enumerate(photos_to_upload, start=1):
                if self._errorcount > errors_allowed:
                    log.critical(
                        'Too many upload errors: %s. Aborting.',
                        self._errorcount)
                    sys.exit(1)
                if self._should_quit.isSet():
                    log.warning('Aborting uploads due to user request.')
                    sys.exit(1)
                thread = threading.Thread(
                    None, self._upload_in_thread, args=[fname])
                thread.setDaemon(True)
                self._semaphore.acquire()
                log.info('%s/%s Uploading %s', index, len(photos_to_upload),
                         fname)
                threads.append(thread)
                thread.start()

            for thread in threads:
                if self._should_quit.isSet():
                    log.warning('Aborting uploads due to user request.')
                    sys.exit(1)
                thread.join()
        except KeyboardInterrupt:
            log.warning('Aborting uploads due to user request.')
            sys.exit(1)

        if self._errorcount:
            log.warning('Finished all uploads with %s errors', self._errorcount)

    def upload_callback(self, filename, progress, is_done):
        """You can implement this print progress for each photo."""
        if not is_done:
            pass

    def get_photoset(self):
        """Get the PhotoSet object for self.setname if it exists."""
        with self._lock:
            if self._photoset:
                return self._photoset
            sets = self.get_all_photosets()
            self._photoset = sets.get(self.setname)
            return self._photoset

    def get_or_create_photoset(self, primary_photo_id):
        """Threadsafe get or create the PhotoSet object for self.setname."""
        with self._lock:
            all_photosets = self.get_all_photosets()
            photoset = self.get_photoset()
            created = False
            if not photoset:
                photoset = PhotoSet.create(
                    self.flickr, self.setname, primary_photo_id)
                all_photosets[photoset.title] = photoset
                self._photoset = photoset
                created = True
            return photoset, created

    def authenticate(self):
        if self._is_authenticated:
            return
        self.flickr.authenticate_console(perms="write")
        self._is_authenticated = True

    def get_all_photosets(self, refresh=False):
        """Return a dict {unicode setname: PhotoSet photoset}. Cached."""

        with self._lock:
            if self._all_photosets and not refresh:
                return self._all_photosets
            result = {}
            log.debug('Requesting all photosets')
            sets_e = self.flickr.photosets_getList()
            for photoset_e in sets_e.find('photosets'):
                photoset = PhotoSet.from_element(self.flickr, photoset_e)
                result[photoset.title] = photoset
            self._all_photosets = result
            return self._all_photosets

    def get_title(self, filename):
        """Get photo title from filename."""
        filename = os.path.basename(filename)
        return unicode_path(filename)

    def get_photos_to_upload(self):
        """Get a list of filenames that should be uploaded to flickr."""
        photos_to_upload = []
        for fname in os.listdir(self.dirname):
            # Ignore hidden files.
            if fname.startswith('.'):
                log.debug('Ignoring hidden file "%s"', fname)
                continue
            fname = os.path.join(self.dirname, fname)
            if not os.path.isfile(fname):
                continue
            if not self.PHOTO_RE.match(fname):
                continue
            photos_to_upload.append(fname)
        photos_to_upload.sort()
        return photos_to_upload

    def upload(self, filename):
        """Upload filename to flickr."""
        pset = self.get_photoset()
        title = self.get_title(filename)
        if pset and pset.has_photo(title=title):
            log.info('Photo with title "%s" already exists in set "%s"',
                     title, pset.title)
            return
        is_public = '1' if self.is_public else '0'
        photo = self.flickr.upload(
            force_fs_encoding(filename), title=force_utf8(title),
            is_public=is_public, callback=functools.partial(
                self.upload_callback, filename), tags=self.tags)
        photo_id = photo.find('photoid').text
        log.debug('Uploaded %s', filename)
        if not pset:
            pset, created = self.get_or_create_photoset(
                primary_photo_id=photo_id)
        pset.add_photo(photo_id)

    def _upload_in_thread(self, filename):
        """The thread target to upload filename."""
        try:
            self.upload(filename)
        except KeyboardInterrupt:
            self._should_quit.set()
            threading.thread.interrupt_main()
            return
        except:
            log.exception('Error uploading "%s"', filename)
            self._errorcount += 1
        finally:
            self._semaphore.release()


USAGE = """%(prog)s dirname

See --help for details.
"""

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        usage=USAGE, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('dirname',
                        help='The directory, from which to upload photos')
    parser.add_argument('-s', '--setname',
                        help='The name of the set to add the photos to. If not'
                             ' provided, defaults to directory basename.')
    parser.add_argument('-t', '--tags',
                        help='Optional space-delimited tags.')
    parser.add_argument('--threads', type=int,
                        default=4, help='How many concurrent uploads to do')
    parser.add_argument('-p', '--public', action='store_true',
                        help='By default, all photos are stored as private. '
                             'Set this flag to make uploaded photos public.')

    loglevel = logging.INFO
    logformat = '%(asctime)s - %(levelname)s - %(message)s'
    
    logging.basicConfig(level=loglevel, format=logformat)

    args = parser.parse_args()

    if not args.dirname:
        parser.print_usage()
        sys.exit(1)

    assert args.threads > 0

    args.dirname = args.dirname.rstrip(os.sep)

    uploader = MultithreadedUploader(
        args.dirname, setname=args.setname, tags=args.tags,
        threads=int(args.threads), is_public=args.public)

    uploader.run()
