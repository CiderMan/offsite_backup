#!/usr/bin/python
PYTHONIOENCODING="utf-8"

import sys, os, hashlib, subprocess, shutil
from textwrap import TextWrapper

assert os.stat_float_times()

def print_diag(level, value, linefeed = True):
    if level < config.verbosity:
        try:
            # If unicode, convert to UTF-8 string
            value = value.encode("utf-8", "replace")
        except:
            pass
        try:
            print str(value),
        except UnicodeEncodeError:
            # OK, failed to output a UTF-8 string so try plain ASCII
            value = value.encode("ascii", "replace")
            print str(value),
        if linefeed:
            print

CRITICAL, IMPORTANT, INFOMATION, DEBUG, EXTRA_DEBUG = range(5)

UNCHANGED, NEW, UPDATED, BAD_BBF = range(4)

defaults = {
    "sourceBase": (None, "String (required) - The base directory containing content to backup"),
    "backupBase": (None, "String (required) - The base directory to which the backup files should be written"),
    "stateBase": (None, "String (required) - The base directory where the backup state will be stored"),
    "sourceSubDir": (None, "String or list of Strings - The sub-directory/ies of the 'sourceBase' which will be considered for backup"),
    "useTimestamp": (True, "Boolean - Use the file modification time to determine whether it has been changed"),
    "useMd5": (False, "Boolean - Use a file's MD5 hash to determine whether it has been changed"),
    "batchSize": (10, "Integer - The number of out-of-date files to process in one go"),
    "preBatchCmd": (None, "String - The command to run prior to perform the back up of a batch of files. For example, this could be to mount a webdav file system"),
    "postBatchCmd": (None, "String - The command to run after performing the back up of a batch of files. For example, this could be to unmount a webdav file system"),
    "password": (None, "String - Password to use for the 7zip backup file"),
    "includeExtensions": (None, "List of Strings - The file extensions to back up. If not set, all files are included"),
    "excludeExtensions": (None, "List of Strings - The file extensions to omit from the back up. If not set, no files are excluded"),
    "verbosity": (2, "Integer (0-5) - Amount of information to output. 0 results in no output"),
}

class ConfigOptionException(Exception): pass

class BadConfigOptionException(ConfigOptionException): pass
class ConfigOptionNotSetException(ConfigOptionException): pass

class Config(object):
    def __init__(self, defaults):
        self._defaults = defaults
        self._config = {}

    def __call__(self, filename):
        for k, v in self._defaults.items():
            self._config[k] = v[0]
        execfile(filename, {}, self._config)
        for k in self._config.keys():
            assert k in self._defaults.keys(), "'%s' is not a valid configuration option" % k
            if self._config[k] is None:
                del self._config[k]

    def __getattr__(self, attr):
        try:
            return self._config[attr]
        except KeyError:
            if attr in self._defaults.keys():
                raise ConfigOptionNotSetException("'%s' has not been set" % attr)
            else:
                raise BadConfigOptionException("'%s' is not a valid configuration option" % attr)

    def __str__(self):
        return str(self._config)

config = Config(defaults)

if len(sys.argv) != 2:
    textWrapper = TextWrapper(initial_indent = "    ", width = 78)
    print >> sys.stderr, """Usage: %s <config file>

The config file is a python script setting some or all of the following variables:
""" % sys.argv[0]
    for k, v in sorted(defaults.items()):
        print >> sys.stderr, "  " + k + ":"
        o = v[1].find("-")
        if o < 0:
            textWrapper.subsequent_indent = "        "
        else:
            textWrapper.subsequent_indent = " " * (6 + o)
        print >> sys.stderr, "\n".join(textWrapper.wrap(v[1]))
        if v[0] is not None:
            print >> sys.stderr, "      Default:", repr(v[0])
    sys.exit()
else:
    config(sys.argv[1])

try:
    config.sourceBase
    config.backupBase
    config.stateBase
except Exception, e:
    print >> sys.stderr, "Invalid config:", str(e)

def process_batch(batch):
    print_diag(INFOMATION, "Starting batch")
    # Firstly, delete any BBF files so that any subsequent failures will not cause a false
    # negative on future runs
    for src, bbf, backup, sig in batch:
        if os.path.isfile(bbf):
            print_diag(INFOMATION, "Deleting %s" % bbf)
            os.remove(bbf)

    # Now, perform the pre-batch stage
    try:
        process = subprocess.Popen(config.preBatchCmd, shell = True)
        retcode = process.wait()
        if retcode != 0:
            print_diag(CRITICAL, "Pre-batch command failed with return code %d" % retcode)
            sys.exit(1)
    except ConfigOptionNotSetException:
        pass

    # Now, do the back up itself
    for src, bbf, backup, sig in batch:
        print_diag(INFOMATION, "Backing up %s" % src)
        name = os.path.basename(src)
        archive = os.path.join(os.path.dirname(bbf), name + ".7z") # TODO
        # Create the 7zip command 9as quiet as possible - though still not very quiet)
        # and use maximum (not ultra due to memory use) TODO: use store for some file types?
        cmd = ["7z", "-bd", "-mx=7"]
        try:
            cmd += ["-p" + config.password]
        except ConfigOptionNotSetException:
            pass
        # Add volume splitting if neccessary
        # "-v100m"
        cmd += ["a", archive, src]
        print_diag(DEBUG, "Compression command: " + " ".join(cmd))
        process = subprocess.Popen(cmd)
        retcode = process.wait()
        if retcode != 0:
            print_diag(CRITICAL, "Compression failed with return code %d" % retcode)
            sys.exit(1)
        backupDir = os.path.dirname(backup)
        if not os.path.exists(backupDir):
            os.makedirs(backupDir)
        shutil.move(archive, backupDir)
    
    # Now, perform the post-batch stage
    try:
        process = subprocess.Popen(config.postBatchCmd, shell = True)
        retcode = process.wait()
        if retcode != 0:
            print_diag(CRITICAL, "Post-batch command failed with return code %d" % retcode)
            sys.exit(1)
    except ConfigOptionNotSetException:
        pass
    
    # Finally, write the bbf files
    for src, bbf, backup, sig in batch:
        print_diag(INFOMATION, "Creating %s" % bbf)
        d = os.path.dirname(bbf)
        if not os.path.exists(d):
            os.makedirs(os.path.dirname(bbf))
        with file(bbf, "w") as f:
            f.write(repr(sig))

    print_diag(INFOMATION, "Batch done")

try:
    subDirs = config.sourceSubDir
except ConfigOptionNotSetException:
    subDirs = '.'

try:
    # If subDirs has a strip() method, assume that it is a string-like
    # object and enclose it in a list. Otherwise, assume that subDirs
    # is already some kind of iterable
    subDirs.strip
    subDirs = [subDirs]
except AttributeError:
    pass

_TimeStampTolerance = 0.1 # seconds

batch = []

includeExtensions = []
try:
    for e in config.includeExtensions:
        if e.startswith("."):
            includeExtensions.append(e)
        else:
            includeExtensions.append("." + e)
except ConfigOptionNotSetException:
    includeExtensions = None

excludeExtensions = []
try:
    for e in config.excludeExtensions:
        if e.startswith("."):
            excludeExtensions.append(e)
        else:
            excludeExtensions.append("." + e)
except ConfigOptionNotSetException:
    excludeExtensions = None

for relDir in subDirs:
    for dirName, subDirs, files in os.walk(os.path.join(config.sourceBase, relDir)):
        for f in files:
            ext = os.path.splitext(f)[1]
            if includeExtensions is not None and ext not in includeExtensions:
                continue
            if excludeExtensions is not None and ext in excludeExtensions:
                continue

            status = UNCHANGED
            r = os.path.relpath(dirName, config.sourceBase)
            bbf = os.path.normpath(os.path.join(config.stateBase, r, f + ".bbf"))
            backup = os.path.normpath(os.path.join(config.backupBase, r, f))
            src = os.path.normpath(os.path.join(dirName, f))

            md5Hash = ""
            if config.useMd5:
                md5 = hashlib.md5()
                with file(src, "rb") as f:
                    b = f.read(10240)
                    while len(b) > 0:
                        md5.update(b)
                        b = f.read(10240)
                md5Hash = md5.hexdigest()
            mTime = os.path.getmtime(src)

            if os.path.isfile(bbf):
                try:
                    oldTime, oldHash = eval(file(bbf).read().strip())
                    timeDiff = abs(mTime - oldTime)
                    if (config.useTimestamp and timeDiff > _TimeStampTolerance) or \
                       (config.useMd5 and md5Hash != oldHash):
                        print_diag(INFOMATION, "'%s' has been changed" % src)
                        status = UPDATED
                except:
                    print_diag(CRITICAL, "'%s' is not a valid bbf!" % bbf)
                    status = BAD_BBF
            else:
                print_diag(INFOMATION, "'%s' is a new file" % src)
                status = NEW

            if status != UNCHANGED:
                batch.append((src, bbf, backup, (mTime, md5Hash)))
                if len(batch) >= config.batchSize:
                    process_batch(batch)
                    batch = []

if len(batch) > 0:
    process_batch(batch)

if False:
    def moveFiles(srcDir, dstDir):
        listOfFiles = os.listdir(srcDir)
        for file in listOfFiles:
            srcFile = os.path.join(srcDir, file)
            shutil.copy(srcFile, dstDir)
            os.remove(srcFile)

    def processFile(filename, targetDir, sourceDir, tmpDir):
        # Compression options
        password = 'ph0t0sn3v1ns'
        volSize = ''
        
        # Get relative (to sourceDir) filename
        relPathFilename = os.path.relpath(filename, sourceDir)

        # Filaname for compressed file with correct extension
        cmpFilename = os.path.join(targetDir, relPathFilename)
        cmpFilename, fileExtension = os.path.splitext(cmpFilename)
        cmpFilename = cmpFilename + '.7z'
        if Verbose: print 'Target file:', cmpFilename

        # Mount webDav
        mountWebDav(cmpFilename)

        # Create directory to path to file in target directory if doesn't exist
        # already
        cmpDir = os.path.dirname(cmpFilename);
        if not os.path.exists(cmpDir):
          if Verbose: print 'Creating dir:', cmpDir
          os.makedirs(cmpDir)

        # Temp filename
        tmpFile = os.path.join(tmpDir, os.path.basename(cmpFilename))
        if Verbose: print 'Tmp file:', tmpFile

        # Compress it
        try:
            # Compress will fail if target zip file exists. This is OK because there shouldn't be anything in the
            # tmp directory. User's problem to clear out if there is.
            if Verbose: print 'Compressing', tmpFile, relPathFilename
            # 7zr doesn't do passwords, but 7z does
            # 7z  -mx9 -mhe=on -ptest a test.7z Ma\ and\ Grandma.jpg
            # 7z -v10k -mx9 -mhe=on -ptest a test.7z Ma\ and\ Grandma.jpg
            # 7z options:
            # -bd  Disable percentage indicator
            # -mhe=on|off 7z format only : enables or disables archive header encryption (Default : off)
            # -p{Password} Set Password
            # -v{Size}[b|k|m|g] Create volumes: -v100m
            # -mx[0 | 1 | 3 | 5 | 7 | 9 ] Sets level of compression: -mx9
            # -v100
            # a add
            # Box.net limit: 100MB, 102400KB
            cmpProcess = subprocess.Popen(["7z", "-bd", "-mhe=on", "-mx9", "-v100m", "-p" + password, "a",
                                            tmpFile, relPathFilename], cwd = sourceDir)
            retcode = cmpProcess.wait()
            if retcode < 0:
                print >>sys.stderr, "Child was terminated by signal", -retcode
            elif retcode == 0:
                # Exit ok
                pass
            else:
                print >>sys.stderr, "Child returned", retcode
                raise OSError(retcode, "Compress process error")
        except OSError, e:
            print >>sys.stderr, "Execution failed:", e
            raise
        else:
            if Verbose: print "Compressed:", relPathFilename

        # Copy from tmpDir to targetDir in correct directory
        try:
            moveFiles(tmpDir, cmpDir)
        except shutil.Error, e:
            print >>sys.stderr, "Move failed:", e
            raise
        except IOError, e:
            print >>sys.stderr, "Move failed:", e
            raise
        except:
            print >>sys.stderr, "Move failed"
        else:
            if Verbose: print "Moved to:", cmpFilename

        # Umount WebDav
        umountWebDav(cmpFilename)

    # Delete lock file
    def delLock(lockFile):
        os.remove(lockFile)

    def gen_updated_files(baseDir, dbDir):
        for dirPath, dirNames, fileNames in os.walk(baseDir):
            for f in fileNames:
                status = UNCHANGED
                bbf = os.path.join(dbDir, os.path.relpath(dirPath, baseDir), f + ".bbf")
                src = os.path.join(dirPath, f)
                if os.path.isfile(bbf):
                    mTime = os.stat(src).st_mtime
                    try:
                        oldTime = float(file(bbf).read().strip())
                        timeDiff = abs(mTime - oldTime)
                        if timeDiff > _TimeStampTolerance:
                            status = CHANGED
                    except:
                        status = BAD_BBF
                else:
                    status = NEW

                y = (status, src, [])

                if status != UNCHANGED:
                    yield y

                if len(y[2]) > 0:
                    for l in y[2]:
                        print l
                    status = UNCHANGED # Not true but prevents updating of the BBF

                if status != UNCHANGED:
                    bbfDir = os.path.dirname(bbf)
                    if not os.path.isdir(bbfDir):
                        os.makedirs(bbfDir)
                    with file(bbf, "w") as f:
                        stat = os.stat(src)
                        f.write(str(stat.st_mtime))

    if __name__ == "__main__":
        Verbose = True
     
        # Lock file
        lockFile = os.path.normpath("/home/bill/.sync-lock")
        if os.path.exists(lockFile):
            print "Check already running"
            exit(1)
        if Verbose: print "Creating lock file"
        atexit.register(delLock, lockFile)
        open(lockFile,'w').close()

        # Shutdown file. If present exit cleanly as soon as possible
        shutdownFile = os.path.normpath("/home/bill/.sync-exit")
        if Verbose: print "Shutdown file:", shutdownFile
        if os.path.exists(shutdownFile):
            print "Exiting early because shutdown file present."
            print "Delete shutdown file:", shutdownFile
            exit(1)

        # Box mount point
        boxMountPoint = '/media/box.net'

        # Box root
        boxDir = boxMountPoint
        #boxDir = os.path.normpath("/home/bill/box_sim")
        if Verbose: print "Box dir:", boxDir
        atexit.register(umountWebDav, boxDir)

        # sourceDir = os.path.normpath("/media/storage/pictures")
        sourceDir = os.path.normpath("/home/bill/2002")
        if Verbose: print "Source dir:", sourceDir

        # Remote target dir
        targetDir = os.path.join(boxDir, "pictures")
        if Verbose: print "Box target dir:", targetDir

        os.path.join(boxDir, "Box BBF backup dir:")
        bbfBackupDir = os.path.join(boxDir, "bbf_backup")
        if Verbose: print "Box BBF backup dir:", bbfBackupDir

        tmpDir = os.path.normpath("/home/bill/tmp/sync")
        if not os.path.exists(tmpDir):
          if Verbose: print 'Creating tmp dir:', tmpDir
          os.makedirs(tmpDir)
        else:
          if Verbose: print 'Checking tmpDir is empty'
          if not os.listdir(tmpDir) == []:
            print "tmpDir is'nt empty:", tmpDir
            exit(1)

        dbDirRoot = os.path.normpath("/home/bill/bbf_db")
        dbDir = os.path.join(dbDirRoot, "pictures")
        if Verbose: print 'DB dir:', dbDir
        if not os.path.exists(dbDir):
          if Verbose: print 'Creating DB dir:', dbDir
          os.makedirs(dbDir)

        new = updated = errors = 0

        # exit(1)
        for status, filename, errs in gen_updated_files(sourceDir, dbDir):
            if status == NEW:
                new += 1
                if Verbose: print 'New file:', filename
                try:
                     processFile(filename, targetDir, sourceDir, tmpDir)
                except:
                    print 'Error processing file:', filename
                    errs.append("Error: %s" % filename)
                    umountWebDav(targetDir)
            elif status == UPDATED:
                updated += 1
                if Verbose: print 'Updated:', filename
                processFile(filename, targetDir, sourceDir)
            elif status == BAD_BBF:
                updated += 1
                print 'Error:', filename
            if os.path.exists(shutdownFile):
                print 'Exiting due to shutdown file:', shutdownFile
                break

        print new, "new files,", updated, "changed files:", errors, "errors were encountered"

        # Backup BBF files
        # zip them and copy to box

    # To do
    # Mount webdav if not already done. Function to test and mount if necessary
    # Unmount when finished?
    # What about sync, i.e how do we know it has finished copying
    # Keep track of amount of data transfered. To allow rate limiting so only so much transfer.
    # Looks like have to unmount after each write to ensure uploaded

