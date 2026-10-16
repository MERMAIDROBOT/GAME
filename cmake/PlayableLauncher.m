// Local playback shell. Game engine and asset paths are supplied by the package.
#import <AppKit/AppKit.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/file.h>
#include <stdio.h>

static int fail(NSString *message) {
    [NSApplication sharedApplication];
    NSAlert *alert = [NSAlert new];
    alert.messageText = @"Unable to open the game";
    alert.informativeText = message;
    [alert runModal];
    return 2;
}
int main(void) {
    @autoreleasepool {
        NSBundle *app = NSBundle.mainBundle;
        NSString *bundle = app.bundlePath;
        NSDictionary *c = [NSDictionary dictionaryWithContentsOfFile:
            [bundle stringByAppendingPathComponent:@"Contents/Resources/RunConfiguration.plist"]];
        NSFileManager *fm = NSFileManager.defaultManager;
        NSString *elf = c[@"Executable"], *disc = c[@"DiscDirectory"], *log = c[@"LogFile"];
        if (!elf.isAbsolutePath || !disc.isAbsolutePath || !log.isAbsolutePath)
            return fail(@"The local game configuration is missing or invalid.");
        if (![fm fileExistsAtPath:elf] || ![fm fileExistsAtPath:disc])
            return fail(@"The local disc copy cannot be found. Restore the project data to its original location; the app does not download game files.");
        [fm createDirectoryAtPath:log.stringByDeletingLastPathComponent
            withIntermediateDirectories:YES attributes:nil error:nil];
        // Keep a second Finder click from opening another copy of this package.
        int lock = open([[log stringByAppendingString:@".lock"] fileSystemRepresentation], O_CREAT | O_RDWR, 0600);
        if (lock < 0) return fail(@"The local game log directory is not writable.");
        if (flock(lock, LOCK_EX | LOCK_NB) != 0) {
            for (NSRunningApplication *running in [NSRunningApplication runningApplicationsWithBundleIdentifier:app.bundleIdentifier])
                if (running.processIdentifier != getpid()) [running activateWithOptions:NSApplicationActivateIgnoringOtherApps];
            return 0;
        }
        // Never inherit tinting, automated input, mute, traces or diagnostic modes.
        for (NSString *key in NSProcessInfo.processInfo.environment)
            if ([key hasPrefix:@"PS2_PORT_"] || [key hasPrefix:@"PS2X_"]) unsetenv(key.UTF8String);
        NSDictionary *settings = c[@"Environment"];
        for (NSString *key in settings) setenv(key.UTF8String, [settings[key] UTF8String], 1);
        setenv("PS2_PORT_IO_ROOT", disc.fileSystemRepresentation, 1);
        setenv("PS2_PORT_GS_THREAD", [c[@"RendererWorker"] boolValue] ? "1" : "0", 1);
        setenv("PS2_PORT_ASYNC_PRESENT", [c[@"AsyncPresentation"] boolValue] ? "1" : "0", 1);
        setenv("PS2_PORT_PRIVATE_GIF_BATCH", [c[@"RendererPacketBatch"] boolValue] ? "1" : "0", 1);
        NSString *deinterlace = [c[@"RendererDeinterlace"] stringValue] ?: @"1";
        setenv("PS2_PORT_PRIVATE_DEINTERLACE", deinterlace.UTF8String, 1);
        NSString *frameworks = [bundle stringByAppendingPathComponent:@"Contents/Frameworks"];
        for (NSArray *library in @[@[@"PS2_PORT_PRIVATE_GS_PLUGIN", @"liblocal-parallel-gs-plugin.dylib"],
                                   @[@"PS2_PORT_PRIVATE_MOLTENVK", @"libMoltenVK.dylib"]]) {
            NSString *path = [frameworks stringByAppendingPathComponent:library[1]];
            if (![fm fileExistsAtPath:path]) return fail(@"A bundled graphics library is missing. Recreate the app from the preserved release manifest.");
            setenv([library[0] UTF8String], path.fileSystemRepresentation, 1);
        }
        int fd = open(log.fileSystemRepresentation, O_CREAT | O_WRONLY | O_TRUNC, 0600);
        if (fd < 0) return fail(@"The game log cannot be opened.");
        dup2(fd, STDOUT_FILENO); dup2(fd, STDERR_FILENO); close(fd);
        fprintf(stderr, "[playable launcher] build=%s bundle=%s\n", [c[@"BuildIdentifier"] UTF8String], bundle.fileSystemRepresentation);
        if (chdir("/") != 0) return fail(@"The working directory could not be initialized.");
        NSString *runner = [bundle stringByAppendingPathComponent:@"Contents/MacOS/ps2EntryRunner"];
        char *args[] = {(char *)runner.fileSystemRepresentation, (char *)elf.fileSystemRepresentation, NULL};
        execv(args[0], args);
        perror("launch game");
        return fail(@"The game executable could not start. See the local release log.");
    }
}
