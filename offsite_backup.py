#!/usr/bin/python
PYTHONIOENCODING="utf-8"

import sys, os, hashlib, subprocess, shutil, datetime, signal
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
    "tmpDir": (None, "String - The directory where the backup archives will be created before being moved to their final destination"),
    "sourceSubDir": (None, "String or list of Strings - The sub-directory/ies of the 'sourceBase' which will be considered for backup"),
    "useTimestamp": (True, "Boolean - Use the file modification time to determine whether it has been changed"),
    "useMd5": (False, "Boolean - Use a file's MD5 hash to determine whether it has been changed"),
    "batchSize": (10, "Integer - The number of out-of-date files to process in one go"),
    "preBatchCmd": (None, "String - The command to run prior to perform the back up of a batch of files. For example, this could be to mount a webdav file system"),
    "postBatchCmd": (None, "String - The command to run after performing the back up of a batch of files. For example, this could be to unmount a webdav file system"),
    "password": (None, "String - Password to use for the 7zip backup file"),
    "includeExtensions": (None, "List of Strings - The file extensions to back up. If not set, all files are included"),
    "excludeExtensions": (None, "List of Strings - The file extensions to omit from the back up. If not set, no files are excluded"),
    "storeExtensions": (None, "List of Strings - The file extensions that should not be compressed when being backed up. They will still be placed in a 7zip archive, so volumne splitting and encryption are still supported, but 7zip will not attempt to compress the file"),
    "stopDuration": (None, "Integer - Maximum number of minutes of seconds to run. If, at the end of a batch, the script has been running for more than this number of seconds, it will exit. Note that this means that not all files will have been backed up until the script has been run again... and again and again, potentially!"),
    "stopTime": (None, "Integer or Tuple of 2 Integers - The time (either hour or hour and minutes) in 24hr clock at which the script will stop. It will only stop after completing a batch so may actually run for a while after this time"),
    "maxFileSize": (None, "Integer or String - The maximum file size for the backup. Backup files bigger than that will be split into multiple volumes and placed in a directory named after the backed-up file. Can be the maximum size in bytes or a string suitable to pass to 7zip's -v option"),
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

def signal_to_exception(sig, frame):
    for attribute in dir(signal):
        if attribute.startswith("SIG"):
            if getattr(signal, attribute) is sig:
                raise Exception("Got signal %s (%d)" % (attribute, sig))
    raise Exception("Got signal %d" % sig)

hook = ["SIGTERM", "SIGINT", "SIGHUP"]
for sig in hook:
    signal.signal(getattr(signal, sig), signal_to_exception)
    print_diag(EXTRA_DEBUG, "Hooked " + sig)

try:
    config.sourceBase
    config.backupBase
    config.stateBase
except Exception, e:
    print >> sys.stderr, "Invalid config:", str(e)

def process_batch(batch, storeExtensions, volSize, tmpDir):
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
            print_diag(CRITICAL, "*** Pre-batch command failed with return code %d" % retcode)
            sys.exit(1)
    except ConfigOptionNotSetException:
        pass

    # Now, do the back up itself
    try:
        for src, bbf, backup, sig in batch:
            print_diag(INFOMATION, "Backing up %s" % src)
            name = os.path.basename(backup)
            archive = os.path.join(tmpDir, name)
            archiveName = archive
            # Create the 7zip command (as quiet as possible - though still not very quiet)
            # and use maximum (not ultra due to memory use)
            mx = 7
            if storeExtensions is not None:
                if os.path.splitext(src)[1] in storeExtensions:
                    mx = 0
            cmd = ["7z", "-bd", "-mx=%d" % mx]
            try:
                cmd += ["-p" + config.password]
            except ConfigOptionNotSetException:
                pass
            if volSize is not None:
                # Add volume splitting if neccessary
                cmd += ["-v%s" % str(volSize)]
                if not os.path.isdir(archive):
                    os.makedirs(archive)
                archiveName = os.path.join(archive, os.path.basename(archive))
            cmd += ["a", archiveName, src]
            print_diag(DEBUG, "Compression command: " + " ".join(cmd))
            process = subprocess.Popen(cmd)
            retcode = process.wait()
            if retcode != 0:
                print_diag(CRITICAL, "*** Compression failed with return code %d" % retcode)
                sys.exit(1)
            if volSize is not None:
                # Check to see whether there is just one file or not
                fs = os.listdir(archive)
                if len(fs) == 1:
                    # This didn't need to be split into multiple volumes so move the file
                    # into the base directory
                    tmp = os.path.join(os.path.dirname(archive), fs[0])
                    shutil.move(os.path.join(archive, fs[0]), tmp)
                    # Now remove the directory that we created
                    os.rmdir(archive)
                    # And rename the archive to the expected name
                    os.rename(tmp, archive)
            backupDir = os.path.dirname(backup)
            if not os.path.exists(backupDir):
                os.makedirs(backupDir)
            if os.path.isfile(backup):
                os.remove(backup)
            elif os.path.isdir(backup):
                for f in os.listdir(backup):
                    os.remove(os.path.join(backup, f))
                os.rmdir(backup)
            elif os.path.exists(backup):
                print_diag(CRITICAL, "*** Destination exists but is not a file or directory!")
            shutil.move(archive, backupDir)
    except Exception, e:
        print_diag(CRITICAL, "*** Caught exception when backing up!")
        try:
            process = subprocess.Popen(config.postBatchCmd, shell = True)
            retcode = process.wait()
            if retcode != 0:
                print_diag(CRITICAL, "*** Post-batch command failed with return code %d" % retcode)
        except ConfigOptionNotSetException:
            pass
        raise e
        
    # Now, perform the post-batch stage
    try:
        process = subprocess.Popen(config.postBatchCmd, shell = True)
        retcode = process.wait()
        if retcode != 0:
            print_diag(CRITICAL, "*** Post-batch command failed with return code %d" % retcode)
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

excludeExtensions = [".bbf"]
try:
    for e in config.excludeExtensions:
        if e.startswith("."):
            excludeExtensions.append(e)
        else:
            excludeExtensions.append("." + e)
except ConfigOptionNotSetException:
    pass

storeExtensions = []
try:
    for e in config.storeExtensions:
        if e.startswith("."):
            storeExtensions.append(e)
        else:
            storeExtensions.append("." + e)
except ConfigOptionNotSetException:
    storeExtensions = None

startTime = datetime.datetime.now()

try:
    stopDuration = config.stopDuration
except ConfigOptionNotSetException:
    stopTime = config.stopTime
    try:
        stopTime = startTime.replace(hour = stopTime[0], minute = stopTime[1], second = 0)
    except TypeError:
        stopTime = startTime.replace(hour = stopTime, minute = 0, second = 0)
    if stopTime < startTime:
        stopTime += datetime.timedelta(hours = 24)
    stopDuration = stopTime - startTime
    print_diag(INFOMATION, "Will run for up to " + str(stopDuration))
    stopDuration = stopDuration.total_seconds()
except ConfigOptionNotSetException:
    stopDuration = None

try:
    volSize = config.maxFileSize
except ConfigOptionNotSetException:
    volSize = None

try:
    tmpDir = config.tmpDir
except ConfigOptionNotSetException:
    tmpDir = config.stateBase

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
            backup = os.path.normpath(os.path.join(config.backupBase, r, f + ".7z"))
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
                    print_diag(CRITICAL, "*** '%s' is not a valid bbf!" % bbf)
                    status = BAD_BBF
            else:
                print_diag(INFOMATION, "'%s' is a new file" % src)
                status = NEW

            if status != UNCHANGED:
                batch.append((src, bbf, backup, (mTime, md5Hash)))
                if len(batch) >= config.batchSize:
                    process_batch(batch, storeExtensions, volSize, tmpDir)
                    batch = []
                    if stopDuration is not None:
                        if (datetime.datetime.now() - startTime).total_seconds() >= stopDuration:
                            print_diag(IMPORTANT, "** Exiting as 'stopDuration' or 'stopTime' has been exceeded.\n"
                                                  "** Not all files have checked for backup")
                            sys.exit(0)

if len(batch) > 0:
    process_batch(batch, storeExtensions, volSize, tmpDir)

if False:
    # Delete lock file
    def delLock(lockFile):
        os.remove(lockFile)

    if __name__ == "__main__":
        # Lock file
        lockFile = os.path.normpath("/home/bill/.sync-lock")
        if os.path.exists(lockFile):
            print "Check already running"
            exit(1)
        if Verbose: print "Creating lock file"
        atexit.register(delLock, lockFile)
        open(lockFile,'w').close()

