// Original vector icon, drawn locally with AppKit. No game artwork or network.
#import <AppKit/AppKit.h>
int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc < 2) return 2;
        NSImage *image = [[NSImage alloc] initWithSize:NSMakeSize(1024,1024)];
        [image lockFocus];
        NSRect bounds=NSMakeRect(32,32,960,960);
        [[NSBezierPath bezierPathWithRoundedRect:bounds xRadius:205 yRadius:205] addClip];
        NSColor *dark=[NSColor colorWithSRGBRed:.03 green:.14 blue:.29 alpha:1];
        NSColor *light=[NSColor colorWithSRGBRed:.02 green:.55 blue:.66 alpha:1];
        [[[NSGradient alloc] initWithStartingColor:dark endingColor:light] drawInRect:bounds angle:80];
        [[NSColor colorWithSRGBRed:1 green:.78 blue:.38 alpha:1] setFill];
        [[NSBezierPath bezierPathWithOvalInRect:NSMakeRect(665,660,160,160)] fill];
        NSBezierPath *wave=[NSBezierPath bezierPath];
        [wave moveToPoint:NSMakePoint(0,245)];
        [wave curveToPoint:NSMakePoint(655,700) controlPoint1:NSMakePoint(300,190) controlPoint2:NSMakePoint(340,785)];
        [wave curveToPoint:NSMakePoint(670,470) controlPoint1:NSMakePoint(855,655) controlPoint2:NSMakePoint(805,460)];
        [wave curveToPoint:NSMakePoint(730,560) controlPoint1:NSMakePoint(725,475) controlPoint2:NSMakePoint(750,520)];
        [wave curveToPoint:NSMakePoint(460,400) controlPoint1:NSMakePoint(740,720) controlPoint2:NSMakePoint(455,655)];
        [wave curveToPoint:NSMakePoint(1024,260) controlPoint1:NSMakePoint(495,220) controlPoint2:NSMakePoint(880,400)];
        [wave lineToPoint:NSMakePoint(1024,0)]; [wave lineToPoint:NSMakePoint(0,0)]; [wave closePath];
        [[NSColor colorWithSRGBRed:.22 green:.87 blue:.88 alpha:1] setFill]; [wave fill];
        [[NSColor colorWithSRGBRed:.86 green:.99 blue:.96 alpha:1] setStroke]; [wave setLineWidth:15]; [wave stroke];
        NSString *title=argc>2 ? @(argv[2]) : @"KS";
        [title drawAtPoint:NSMakePoint(130,740) withAttributes:@{NSFontAttributeName:[NSFont systemFontOfSize:155 weight:NSFontWeightHeavy], NSForegroundColorAttributeName:NSColor.whiteColor, NSKernAttributeName:@5}];
        [image unlockFocus];
        NSBitmapImageRep *rep=[NSBitmapImageRep imageRepWithData:image.TIFFRepresentation];
        return [[rep representationUsingType:NSBitmapImageFileTypePNG properties:@{}] writeToFile:@(argv[1]) atomically:YES] ? 0 : 3;
    }
}
