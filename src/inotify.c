#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/inotify.h>
#include <sys/stat.h>
#include <errno.h>
#include <limits.h>
#include <syslog.h>
#include "dfr.h"

#define EVENT_SIZE (sizeof(struct inotify_event))
#define BUF_LEN (1024 * (EVENT_SIZE + 16))

static int ifd = -1;
static int wd = -1;

int init_inotify(dfr_config_t *config) {
    ifd = inotify_init();
    if (ifd < 0) {
        syslog(LOG_ERR, "inotify_init failed: %s", strerror(errno));
        return -1;
    }
    
    wd = inotify_add_watch(ifd, config->watch_dir,
                           IN_CREATE | IN_MODIFY | IN_DELETE | 
                           IN_MOVED_FROM | IN_MOVED_TO | IN_CLOSE_WRITE);
    
    if (wd < 0) {
        syslog(LOG_ERR, "inotify_add_watch failed for %s: %s", 
               config->watch_dir, strerror(errno));
        close(ifd);
        return -1;
    }
    
    syslog(LOG_INFO, "Watching directory: %s", config->watch_dir);
    return 0;
}

void* inotify_worker(void *arg) {
    dfr_config_t *config = (dfr_config_t *)arg;
    char buffer[BUF_LEN];
    
    syslog(LOG_INFO, "Inotify worker thread started");
    
    while (1) {
        int length = read(ifd, buffer, BUF_LEN);
        
        if (length < 0) {
            syslog(LOG_ERR, "inotify read error: %s", strerror(errno));
            continue;
        }
        
        int i = 0;
        while (i < length) {
            struct inotify_event *event = (struct inotify_event *)&buffer[i];
            
            if (event->len > 0) {
                dfr_event_t dfr_ev;
                memset(&dfr_ev, 0, sizeof(dfr_ev));
                
                // Build full path
                int needed = snprintf(dfr_ev.path, MAX_PATH_LEN, "%s/%s", 
                        config->watch_dir, event->name);

                        if (needed >= MAX_PATH_LEN) {
                            fprintf(stderr,"Chemin trop long: %s/%s", config->watch_dir, event->name);
                            return NULL;
                        }
                
                // Determine event type
                if (event->mask & IN_CREATE) {
                    dfr_ev.type = EVENT_CREATE;
                } else if (event->mask & (IN_MODIFY | IN_CLOSE_WRITE)) {
                    dfr_ev.type = EVENT_MODIFY;
                } else if (event->mask & IN_DELETE) {
                    dfr_ev.type = EVENT_DELETE;
                } else if (event->mask & (IN_MOVED_FROM | IN_MOVED_TO)) {
                    dfr_ev.type = EVENT_MOVE;
                }
                
                // Get file metadata
                struct stat st;
                if (stat(dfr_ev.path, &st) == 0) {
                    dfr_ev.file_size = st.st_size;
                    dfr_ev.permissions = st.st_mode;
                }
                
                dfr_ev.timestamp = time(NULL);
                strncpy(dfr_ev.node_id, config->node_id, sizeof(dfr_ev.node_id) - 1);
                
                // Read file content for CREATE and MODIFY events
                if ((dfr_ev.type == EVENT_CREATE || dfr_ev.type == EVENT_MODIFY) 
                    && S_ISREG(st.st_mode)) {
                    FILE *fp = fopen(dfr_ev.path, "rb");
                    if (fp) {
                        dfr_ev.data = malloc(st.st_size);
                        if (dfr_ev.data) {
                            dfr_ev.data_len = fread(dfr_ev.data, 1, st.st_size, fp);
                        }
                        fclose(fp);
                    }
                }
                
                // Enqueue event
                if (enqueue_event(&dfr_ev) != 0) {
                    syslog(LOG_WARNING, "Failed to enqueue event for %s", dfr_ev.path);
                    if (dfr_ev.data) free(dfr_ev.data);
                }
                
                log_event("Event detected: %s on %s", 
                         (dfr_ev.type == EVENT_CREATE ? "CREATE" :
                          dfr_ev.type == EVENT_MODIFY ? "MODIFY" :
                          dfr_ev.type == EVENT_DELETE ? "DELETE" : "MOVE"),
                         event->name);
            }
            
            i += EVENT_SIZE + event->len;
        }
    }
    
    return NULL;
}

void cleanup_inotify(void) {
    if (wd >= 0) {
        inotify_rm_watch(ifd, wd);
    }
    if (ifd >= 0) {
        close(ifd);
    }
    syslog(LOG_INFO, "Inotify cleanup completed");
}