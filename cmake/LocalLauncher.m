#include "BundleConfiguration.h"
// Local debug launcher. Reads local paths only; contains no networking code.
#import <Foundation/Foundation.h>
#include <unistd.h>
#include <fcntl.h>
#include <stdio.h>

int main(void) {
    @autoreleasepool {
        NSString *bundle = [[NSBundle mainBundle] bundlePath];
        NSDictionary *config = PortRunConfiguration(NSBundle.mainBundle);
        NSString *elf = config[@"Executable"];
        NSString *build = config[@"BuildIdentifier"];

        NSString *directory = config[@"DiscDirectory"];
        NSString *log = config[@"LogFile"];
        if (!elf || !directory || !log) return 2;
        if (![directory isAbsolutePath]) return 2;
        // Explicit configuration survives Finder launches; inherited diagnostics
        // and automated input never define a reproducible app configuration.
        for (NSString *key in NSProcessInfo.processInfo.environment)
            if ([key hasPrefix:@"PS2_PORT_"] || [key hasPrefix:@"PS2X_"]) unsetenv(key.UTF8String);
        NSDictionary *environment = config[@"Environment"];
        if (environment && ![environment isKindOfClass:NSDictionary.class]) return 2;
        for (NSString *key in environment) {
            if (![key isKindOfClass:NSString.class] || ![key hasPrefix:@"PS2_PORT_"] ||
                ![environment[key] isKindOfClass:NSString.class]) return 2;
            setenv(key.UTF8String, [environment[key] UTF8String], 1);
        }
        NSDictionary *bindings = config[@"FrameworkEnvironment"];
        if (bindings && ![bindings isKindOfClass:NSDictionary.class]) return 2;
        for (NSString *key in bindings) {
            NSString *name = bindings[key];
            if (!([key isEqualToString:@"PS2_PORT_PRIVATE_GS_PLUGIN"] || [key isEqualToString:@"PS2_PORT_PRIVATE_MOLTENVK"]) ||
                ![name isKindOfClass:NSString.class] || ![name isEqualToString:name.lastPathComponent]) return 2;
            NSString *path = [[bundle stringByAppendingPathComponent:@"Contents/Frameworks"] stringByAppendingPathComponent:name];
            if (![NSFileManager.defaultManager fileExistsAtPath:path]) return 2;
            setenv(key.UTF8String, path.fileSystemRepresentation, 1);
        }
        setenv("PS2_PORT_PRIVATE_GIF_BATCH", [config[@"RendererPacketBatch"] boolValue] ? "1" : "0", 1);
        NSString *deinterlace = [config[@"RendererDeinterlace"] stringValue] ?: @"1";
        setenv("PS2_PORT_PRIVATE_DEINTERLACE", deinterlace.UTF8String, 1);
        setenv("PS2_PORT_IO_ROOT", [directory fileSystemRepresentation], 1);
        NSString *image = config[@"CdImage"];
        if (image && ![image isAbsolutePath]) return 2;
        if (image) setenv("PS2_PORT_CD_IMAGE", [image fileSystemRepresentation], 1);
        else unsetenv("PS2_PORT_CD_IMAGE");
        int fd = open([log fileSystemRepresentation], O_CREAT | O_WRONLY | O_TRUNC, 0600);
        if (fd < 0) return 3;
        dup2(fd, STDOUT_FILENO);
        dup2(fd, STDERR_FILENO);
        close(fd);
        fprintf(stderr, "[port-lab launcher] build=%s bundle=%s\n",
                build ? [build UTF8String] : "unknown", [bundle fileSystemRepresentation]);
        setenv("PS2_PORT_GS_THREAD", [config[@"RendererWorker"] boolValue] ? "1" : "0", 1);
        fprintf(stderr, "[port-lab launcher] renderer_worker=%d\n", [config[@"RendererWorker"] boolValue]);
        setenv("PS2_PORT_ASYNC_PRESENT", [config[@"AsyncPresentation"] boolValue] ? "1" : "0", 1);
        fprintf(stderr, "[port-lab launcher] async_presentation=%d\n", [config[@"AsyncPresentation"] boolValue]);
        // Interactive launches use the normal execution path. Scenario-only
        // vector capture/verification must not leak in from a parent shell.
        unsetenv("PS2X_VU1_CAPTURE");
        unsetenv("PS2X_VU1_TRACE");
        unsetenv("PS2X_VU1_VERIFY");
        unsetenv("PS2X_VU1_RECOMP");
        unsetenv("PS2_PORT_GS_TRACE");
        unsetenv("PS2_PORT_GS_HISTORY");
        unsetenv("PS2_PORT_VU_TRACE");
        unsetenv("PS2_PORT_GS_PROFILE");
        unsetenv("PS2_PORT_PAD_TRACE");
        // VU programs the game runs are recorded for the next build to compile: the engine learns per title.
        NSString *vuCapture = config[@"VuCaptureDirectory"];
        if (vuCapture) {
            if (![vuCapture isKindOfClass:NSString.class] || ![vuCapture isAbsolutePath]) return 2;
            setenv("PS2X_VU1_CAPTURE", vuCapture.fileSystemRepresentation, 1);
        }
        if ([config[@"LocalDiagnostics"] boolValue]) {
            setenv("PS2_PORT_GS_PROFILE", "1", 1);
            setenv("PS2_PORT_PAD_TRACE", "1", 1);
        }
        const BOOL historyEnabled = [config[@"GraphicsHistoryEnabled"] boolValue];
        fprintf(stderr, "[port-lab launcher] graphics_history=%d\n", historyEnabled);
        if (historyEnabled) {
            setenv("PS2_PORT_GS_TRACE", "1", 1);
            setenv("PS2_PORT_VU_TRACE", "1", 1);
            NSString *history = config[@"GraphicsHistory"];
            if (history) setenv("PS2_PORT_GS_HISTORY", [history fileSystemRepresentation], 1);
        }
        // Asset and save roots are explicit. Window-library getcwd() calls
        // must not traverse the protected game directory during app startup.
        if (chdir("/") != 0) { perror("runtime working directory"); return 4; }
        NSString *runner = [bundle stringByAppendingPathComponent:@"Contents/MacOS/ps2EntryRunner"];
        char *args[] = {(char *)[runner fileSystemRepresentation], (char *)[elf fileSystemRepresentation], NULL};
        execv(args[0], args);
        perror("launch runtime");
        return 5;
    }
}
