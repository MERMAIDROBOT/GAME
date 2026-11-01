/* Bounded immutable getenv snapshots for the native host runtime.
 * Entries and returned strings stay alive for process lifetime. Successful
 * environment mutations advance an epoch; no published entry is overwritten.
 * putenv can expose mutable caller storage, so it permanently disables caching.
 * This replaces launch-only caches that silently ignored setenv/unsetenv.
 */
#include <dlfcn.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

typedef char *(*get_fn)(const char *);
typedef int (*set_fn)(const char *, const char *, int);
typedef int (*unset_fn)(const char *);
typedef int (*put_fn)(char *);
enum { CACHE_SLOTS = 256 };
struct entry { char *key; char *value; uint64_t epoch; };
static struct entry entries[CACHE_SLOTS];
static _Atomic unsigned count;
static _Atomic uint64_t epoch;
static _Atomic int disabled;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;

static char *real_get(const char *name) {
    static _Atomic(get_fn) saved;
    get_fn fn=atomic_load_explicit(&saved,memory_order_acquire);
    if (!fn) { fn=(get_fn)dlsym(RTLD_NEXT,"getenv"); atomic_store_explicit(&saved,fn,memory_order_release); }
    return fn ? fn(name) : NULL;
}
static int lookup(const char *name, unsigned n, uint64_t generation, char **value) {
    for (unsigned i=0;i<n;++i)
        if (entries[i].epoch==generation && !strcmp(entries[i].key,name)) { *value=entries[i].value; return 1; }
    return 0;
}
char *getenv(const char *name) {
    if (!name) return NULL;
    if (atomic_load_explicit(&disabled,memory_order_acquire)) return real_get(name);
    char *value=NULL;
    const uint64_t generation=atomic_load_explicit(&epoch,memory_order_acquire);
    if (lookup(name,atomic_load_explicit(&count,memory_order_acquire),generation,&value)) return value;
    pthread_mutex_lock(&lock);
    const uint64_t current=atomic_load_explicit(&epoch,memory_order_relaxed);
    const unsigned n=atomic_load_explicit(&count,memory_order_relaxed);
    if (atomic_load_explicit(&disabled,memory_order_relaxed)) value=real_get(name);
    else if (!lookup(name,n,current,&value)) {
        value=real_get(name);
        if (n<CACHE_SLOTS) {
            char *key=strdup(name), *copy=value ? strdup(value) : NULL;
            if (key && (!value || copy)) {
                entries[n]=(struct entry){key,copy,current};
                value=copy;
                atomic_store_explicit(&count,n+1,memory_order_release);
            } else { free(key);free(copy); }
        }
    }
    pthread_mutex_unlock(&lock);
    return value;
}
int setenv(const char *name,const char *value,int overwrite) {
    set_fn fn=(set_fn)dlsym(RTLD_NEXT,"setenv");
    if (!fn) { errno=ENOSYS;return -1; }
    pthread_mutex_lock(&lock);
    int result=fn(name,value,overwrite);
    if (!result) atomic_fetch_add_explicit(&epoch,1,memory_order_release);
    pthread_mutex_unlock(&lock);return result;
}
int unsetenv(const char *name) {
    unset_fn fn=(unset_fn)dlsym(RTLD_NEXT,"unsetenv");
    if (!fn) { errno=ENOSYS;return -1; }
    pthread_mutex_lock(&lock);
    int result=fn(name);
    if (!result) atomic_fetch_add_explicit(&epoch,1,memory_order_release);
    pthread_mutex_unlock(&lock);return result;
}
int putenv(char *text) {
    put_fn fn=(put_fn)dlsym(RTLD_NEXT,"putenv");
    if (!fn) { errno=ENOSYS;return -1; }
    pthread_mutex_lock(&lock);
    int result=fn(text);
    if (!result) atomic_store_explicit(&disabled,1,memory_order_release);
    pthread_mutex_unlock(&lock);return result;
}
#if defined(__linux__)
int clearenv(void) {
    typedef int (*clear_fn)(void);
    clear_fn fn=(clear_fn)dlsym(RTLD_NEXT,"clearenv");
    if (!fn) { errno=ENOSYS;return -1; }
    pthread_mutex_lock(&lock);int result=fn();
    if (!result) atomic_fetch_add_explicit(&epoch,1,memory_order_release);
    pthread_mutex_unlock(&lock);return result;
}
#endif
