#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
int main(int argc, char **argv) {
 if(argc==2 && !strcmp(argv[1],"mutable")) {
  assert(getenv("PORT_ENV_CACHE_MUTABLE")==NULL);
  char mutable[]="PORT_ENV_CACHE_MUTABLE=alpha";assert(putenv(mutable)==0);
  assert(!strcmp(getenv("PORT_ENV_CACHE_MUTABLE"),"alpha"));
  memcpy(strchr(mutable,'=')+1,"bravo",5);assert(!strcmp(getenv("PORT_ENV_CACHE_MUTABLE"),"bravo"));
  puts("PASS: mutable putenv invalidates a cached miss and observes caller storage before capacity");
  return 0;
 }

 const char *name="PORT_ENV_CACHE_SYNTHETIC";
 unsetenv(name);assert(getenv(name)==NULL);
 assert(setenv(name,"first",1)==0);assert(!strcmp(getenv(name),"first"));
 assert(setenv(name,"second",1)==0);assert(!strcmp(getenv(name),"second"));
 assert(setenv(name,"ignored",0)==0);assert(!strcmp(getenv(name),"second"));
 assert(unsetenv(name)==0);assert(getenv(name)==NULL);
 char key[80]="PORT_ENV_CACHE_A";setenv(key,"A",1);assert(!strcmp(getenv(key),"A"));
 strcpy(key,"PORT_ENV_CACHE_B");setenv(key,"B",1);assert(!strcmp(getenv(key),"B"));
 for(unsigned i=0;i<300;++i) { snprintf(key,sizeof key,"PORT_ENV_CACHE_FILL_%u",i);assert(getenv(key)==NULL); }
 setenv(name,"after capacity",1);assert(!strcmp(getenv(name),"after capacity"));
 puts("PASS: environment mutations, missing values, reused names, bounded capacity");
}
