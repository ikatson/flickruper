flickruper
==========

Flickr CLI multi-threaded uploader, can resume uploads on failures.

Now that Flickr gives everyone 1TB of space, it's very handy to store
all your original photos there. This tool helps upload lots of files to
Flickr at once.

Installation
============

You can install flickruper with

::

    pip install flickruper

If you don't have pip, you can install it with this command on Debian/Ubuntu

::

    sudo apt-get install python-pip

If not on Debian/Ubuntu, follow the instructions here
http://www.pip-installer.org/en/latest/installing.html

Usage
=====

**flickruper** uploads a single directory at once, without recursion
into subdirectories. It cannot upload a single file or several files.
This may change in the future.

First, place all your photos inside a dir, and, optionally, rename it to
how you want your resulting Flickr set to be named.

Then just

::

    flickruper DIRNAME

If the set exists, it won't upload the same file several times.

If you want tags, and/or other set name, do

::

    flickruper DIRNAME --setname 'Custom set name' --tags 'Tag1 Tag2'

See --help for other options

::

    flickruper --help

**Note:**

At first launch, flickruper requires a browser to get a token. If you
want to use it on a headless server, launch it first on your desktop,
get the token, and copy the **~/.flickr** directory to your server.
