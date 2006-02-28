#!/usr/bin/python

# Written by Ross Cohen
# see LICENSE.txt for license information

import Codeville
from distutils.core import setup
import shutil
import sys
assert sys.version >= '2', "Install Python 2.0 or greater"

scripts = ["cdv", "cdvserver", "cdvpasswd", "cdv-agent", "cdvupgrade"]
plat_ext = []
data_files = [('share/doc/Codeville-' + Codeville.version, ['LICENSE.txt'])]

if sys.platform == 'win32':
    from distutils.core import Extension
    if sys.version < '2.4':
        plat_ext = [Extension("Codeville.winrandom",
                              libraries = ['ws2_32', 'advapi32'],
                              sources = ["src/winrand.c"])]
    for i in xrange(len(scripts)):
        shutil.copy(scripts[i], scripts[i] + '.py')
        scripts[i] = scripts[i] + '.py'
    #os.copy('bin/winrandom.pyd', 'Codeville/winrandom.pyd')
else:
    data_files[0][1].append('cdvserver.conf.sample')

for arg in sys.argv:
    if arg.find('wininst') >= 0:
        data_files = [('', ['LICENSE.txt'])]
        for i in xrange(len(scripts)):
            shutil.copy(scripts[i], scripts[i] + '.py')
            scripts[i] = scripts[i] + '.py'

setup(
    name = "Codeville",
    version = Codeville.version,
    author = "Ross Cohen",
    author_email = "<rcohen@snurgle.org>",
    url = "http://www.codeville.org/",
    license = "BSD",

    packages = ["Codeville", "Codeville/old"],
    ext_modules = plat_ext,

    scripts = scripts,
    data_files = data_files
    )
