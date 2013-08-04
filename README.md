flickrupper
===========

Flickr CLI multi-threaded uploader, can resume uploads on failures, does not upload duplicates.

Now that Flickr gives everyone 1TB of space, it's very handy
to store all your original photos there. This tool helps upload lots of files to Flickr at once.

Installation
============

The only dependency is "flickrapi".

    pip install flickrapi

or, on Debian/Ubuntu

    sudo apt-get install flickrapi


Usage
=====

*flickrupper* uploads a single directory at once, without recursion into subdirectories.
It cannot upload a single file or several files. This is by design, to reduce complexity.

First, place all your photos inside a dir, and, optionally, rename it to how you want your resulting
Flickr set to be named.

Then just

    ./flickrupper.py DIRNAME
    
If the set exists, it won't upload the same file several times.
    
If you want tags, and/or other set name, do

    ./flickrupper.py DIRNAME --setname 'Custom set name' --tags 'Tag1 Tag2'
    
See --help for other options
    
    ./flickrupper.py --help
