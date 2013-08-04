#!/usr/bin/env python
import functools

import os
import logging
import re
import sys
import argparse
import threading

api_key = '19f22b87fad6fdc0be6b2108332f681a'
api_secret = '7ca50772a642b783'

LOGLEVEL = logging.DEBUG
LOGFORMAT = '%(asctime)s - %(name)s:%(lineno)d - %(levelname)s - %(message)s'

logging.basicConfig(level=LOGLEVEL, format=LOGFORMAT)

import flickrapi
from flickrapi.tokencache import LockingTokenCache

log = logging.getLogger(__name__)


class Photo(object):
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
        title = element.find('title').text
        description = element.find('description').text
        attrs = element.attrib
        return cls(api, attrs['id'], title, description, attrs=attrs)

    @classmethod
    def create(cls, api, title, primary_photo_id):
        log.info('Creating photoset "%s", primary photo id: %s',
                 title, primary_photo_id)
        pset = api.photosets_create(
            title=title, primary_photo_id=primary_photo_id)
        pset = pset.find('photoset')
        assert pset is not None, 'cannot find "photoset" element'
        return cls(api, pset.attrib['id'], title, attrs=pset.attrib)

    def _walk(self):
        return self.api.walk_set(self.id)

    def walk(self, refresh=False):
        if self._all_photos and not refresh:
            return self._all_photos
        self._all_photos = []
        for photo in self._walk():
            self._all_photos.append(
                Photo.from_element(api=self.api, element=photo, photoset=self))
        return self._all_photos

    def has_photo(self, title=None, id=None, refresh=False):
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
        if isinstance(photo_or_photo_id, Photo):
            photo_id = photo_or_photo_id.id
        else:
            photo_id = photo_or_photo_id
        if self.has_photo(id=photo_id):
            return
        log.debug('Adding photo %s to photoset "%s', photo_id, self.title)
        self.api.photosets_addPhoto(photoset_id=self.id, photo_id=photo_id)
        self._all_photos.append(Photo(self.api, photo_id, None))

    def __repr__(self):
        result = '<Photoset %s: %s>' % (self.id, self.title)
        if isinstance(result, unicode):
            return result.encode('utf-8')
        return result


class MultithreadedUploader(object):

    PHOTO_RE = re.compile('.*\.(jpg|jpeg|png|gif|tif|tiff)$', re.IGNORECASE)

    def __init__(self, dirname, setname=None, tags=None, threads=4):
        self.dirname = dirname
        if not setname:
            setname = os.path.basename(dirname)
        if not os.path.isdir(dirname):
            raise OSError('Directory "%s" does not exist', dirname)
        self.setname = setname
        self.tags = tags
        self._is_authenticated = False
        self._threadcount = threads
        self.flickr = flickrapi.FlickrAPI(api_key, api_secret)
        self._photoset = None
        self._all_photosets = []
        self._lock = threading.RLock()
        self._semaphore = threading.Semaphore(threads)
        self.authenticate()

    def upload_callback(self, filename, progress, is_done):
        if not is_done:
            log.debug('Uploaded %s%% of %s', progress, filename)

    def get_photoset(self):
        with self._lock:
            if self._photoset:
                return self._photoset
            sets = self.get_all_photosets()
            self._photoset = sets.get(self.setname)
            return self._photoset

    def get_or_create_photoset(self, primary_photo_id):
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
        self.flickr.authenticate_console(perms="writer")
        self._is_authenticated = True

    def get_all_photosets(self, refresh=False):
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
        return os.path.basename(filename)

    def upload(self, filename):
        pset = self.get_photoset()
        title = self.get_title(filename)
        if pset and pset.has_photo(title=title):
            log.info('Photo with title "%s" already exists in set "%s"',
                     title, pset.title)
            return
        log.info('Starting upload of %s', filename)
        photo = self.flickr.upload(
            filename, title=title, is_public=False,
            callback=functools.partial(self.upload_callback, filename))
        photo_id = photo.find('photoid').text
        log.info('Uploaded %s', filename)
        if not pset:
            pset, created = self.get_or_create_photoset(
                primary_photo_id=photo_id)
        pset.add_photo(photo_id)

    def run(self):

        threads = []

        def upload_in_thread(filename):
            try:
                self.upload(filename)
            finally:
                self._semaphore.release()

        for fname in os.listdir(self.dirname):
            if not self.PHOTO_RE.match(fname):
                continue
            fname = os.path.join(self.dirname, fname)
            thread = threading.Thread(None, upload_in_thread, args=[fname])
            self._semaphore.acquire()
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(usage='%(prog)s dirname [setname]')
    parser.add_argument('dirname',
                        help='The directory, from which to upload photos')
    parser.add_argument('setname',
                        help='The name of the set to add the photos to')
    parser.add_argument('--threads', type=int,
                        default=4, help='How many concurrent uploads to do')

    args = parser.parse_args()
    if not args.dirname:
        parser.print_usage()
        sys.exit(1)

    assert args.threads > 0

    if args.setname:
        args.setname = args.setname.decode('utf-8')

    uploader = MultithreadedUploader(args.dirname, setname=args.setname,
                                     threads=int(args.threads))

    uploader.run()