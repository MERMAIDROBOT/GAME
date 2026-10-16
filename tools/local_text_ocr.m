#import <Foundation/Foundation.h>
#import <Vision/Vision.h>
#import <ImageIO/ImageIO.h>
int main(int argc, char **argv) {
 @autoreleasepool {
  if(argc!=2) return 2;
  NSArray *inputs=[NSJSONSerialization JSONObjectWithData:[NSData dataWithContentsOfFile:@(argv[1])] options:0 error:nil];
  if(!inputs) return 3;
  NSUInteger index=0;
  for(NSDictionary *item in inputs) { @autoreleasepool {
   NSMutableDictionary *record=[@{@"id":item[@"id"],@"observations":@[]} mutableCopy];
   CGImageSourceRef src=CGImageSourceCreateWithURL((__bridge CFURLRef)[NSURL fileURLWithPath:item[@"file"]],NULL);
   CGImageRef image=src?CGImageSourceCreateImageAtIndex(src,0,NULL):NULL;
   NSError *error=nil;
   if(image) {
    VNRecognizeTextRequest *request=[VNRecognizeTextRequest new];
    request.usesCPUOnly=YES; request.recognitionLevel=VNRequestTextRecognitionLevelAccurate;
    request.usesLanguageCorrection=NO; request.recognitionLanguages=@[@"en-US"]; request.minimumTextHeight=0.015;
    VNImageRequestHandler *handler=[[VNImageRequestHandler alloc] initWithCGImage:image options:@{}];
    if([handler performRequests:@[request] error:&error]) {
     NSMutableArray *observations=[NSMutableArray new];
     for(VNRecognizedTextObservation *observation in request.results) {
      VNRecognizedText *top=[[observation topCandidates:1] firstObject];
      if(top) [observations addObject:@{@"text":top.string,@"confidence":@(top.confidence),@"box":@[@(observation.boundingBox.origin.x),@(observation.boundingBox.origin.y),@(observation.boundingBox.size.width),@(observation.boundingBox.size.height)]}];
     }
     record[@"observations"]=observations;
    }
    CGImageRelease(image);
   } else { record[@"error"]=@"Image decode failed"; }
   if(src) CFRelease(src);
   if(error) record[@"error"]=error.localizedDescription;
   NSData *data=[NSJSONSerialization dataWithJSONObject:record options:NSJSONWritingSortedKeys error:nil];
   fwrite(data.bytes,1,data.length,stdout); fputc('\n',stdout); fflush(stdout);
   if(index++%100==0) fprintf(stderr,"Classified %lu/%lu\n",(unsigned long)index,(unsigned long)inputs.count);
  }}
 }
 return 0;
}
