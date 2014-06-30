from distutils.core import setup

setup(
    name='flickruper',
    version='0.1.0',
    author='Igor Katson',
    author_email='igor.katson@gmail.com',
    py_modules=['flickruper'],
    scripts=['bin/flickruper'],
    entry_points={
        'console_scripts': ['flickruper = flickruper:main'],
    },
    url='http://pypi.python.org/pypi/flickruper/',
    license='LICENSE.txt',
    description='Multi-threaded uploader for Flickr',
    long_description=open('README.rst').read(),
    install_requires=[
        "flickrapi>=1.4.4",
    ],
)
