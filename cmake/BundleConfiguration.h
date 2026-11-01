// Resolve immutable bundled assets and separate writable per-app storage.
#import <Foundation/Foundation.h>
static NSDictionary *PortRunConfiguration(NSBundle *app) {
    NSFileManager *fm=NSFileManager.defaultManager;
    NSDictionary *raw=[NSDictionary dictionaryWithContentsOfFile:[app.bundlePath stringByAppendingPathComponent:@"Contents/Resources/RunConfiguration.plist"]];
    if (![raw isKindOfClass:NSDictionary.class]) return nil;
    NSMutableDictionary *result=[raw mutableCopy];
    id original=raw[@"Environment"];
    if (original && ![original isKindOfClass:NSDictionary.class]) return nil;
    NSMutableDictionary *env=original?[original mutableCopy]:[NSMutableDictionary new];
    id mapping=raw[@"BundledPaths"];
    if (mapping && ![mapping isKindOfClass:NSDictionary.class]) return nil;
    NSString *resources=[[app.bundlePath stringByAppendingPathComponent:@"Contents/Resources"] stringByResolvingSymlinksInPath];
    NSSet *allowed=[NSSet setWithArray:@[@"Executable",@"DiscDirectory",@"CdImage",@"PS2_PORT_TEX_PACK"]];
    for (id key in mapping) {
        id relative=mapping[key];
        if (![allowed containsObject:key] || ![relative isKindOfClass:NSString.class] || ![relative length] || [relative isAbsolutePath] || [[relative pathComponents] containsObject:@".."] || [relative containsString:@"\\"]) return nil;
        NSString *path=[[resources stringByAppendingPathComponent:relative] stringByResolvingSymlinksInPath];
        if (![path hasPrefix:[resources stringByAppendingString:@"/"]] || ![fm fileExistsAtPath:path]) return nil;
        if ([key isEqual:@"PS2_PORT_TEX_PACK"]) env[key]=path;
        else result[key]=path;
    }
    id portable=raw[@"PortableStorage"];
    if (portable && ![portable isKindOfClass:NSNumber.class]) return nil;
    if ([portable boolValue]) {
        id caps=raw[@"RuntimeCapabilities"];
        if (![caps isKindOfClass:NSDictionary.class] || ![caps[@"separate_save_root"] isEqual:@YES]) return nil;
        NSString *identifier=app.bundleIdentifier;
        NSCharacterSet *invalid=[[NSCharacterSet characterSetWithCharactersInString:@"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-"] invertedSet];
        if (!identifier.length || identifier.length>201 || [identifier rangeOfCharacterFromSet:invalid].location!=NSNotFound || [identifier hasPrefix:@"."] || [identifier hasPrefix:@"-"]) return nil;
        NSString *support=[NSHomeDirectory() stringByAppendingPathComponent:@"Library/Application Support/PS2 Port Lab"];
        NSString *root=[support stringByAppendingPathComponent:identifier];
        for (NSString *part in @[@"",@"mc0",@"cache",@"cache/gpu-pipelines",@"logs"]) {
            NSString *path=part.length?[root stringByAppendingPathComponent:part]:root;
            if ([[fm attributesOfItemAtPath:path error:nil][NSFileType] isEqual:NSFileTypeSymbolicLink]) return nil;
        }
        if ([[fm attributesOfItemAtPath:support error:nil][NSFileType] isEqual:NSFileTypeSymbolicLink]) return nil;
        for (NSString *part in @[@"mc0",@"cache/gpu-pipelines",@"logs"])
            if (![fm createDirectoryAtPath:[root stringByAppendingPathComponent:part] withIntermediateDirectories:YES attributes:nil error:nil]) return nil;
        env[@"PS2_PORT_SAVE_ROOT"]=[root stringByAppendingPathComponent:@"mc0"];
        env[@"PS2_PORT_HW_GS_PIPELINE_CACHE_DIR"]=[root stringByAppendingPathComponent:@"cache/gpu-pipelines"];
        result[@"LogFile"]=[root stringByAppendingPathComponent:@"logs/game.log"];
    }
    result[@"Environment"]=env;
    return result;
}
