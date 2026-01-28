#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>
#include <errno.h>
#include <syslog.h>
#include <libgen.h>
#include "dfr.h"

static int create_parent_dirs(const char *path) {
    char *path_copy = strdup(path);
    char *dir = dirname(path_copy);
    
    struct stat st;
    if (stat(dir, &st) == 0) {
        free(path_copy);
        return 0;
    }
    
    char cmd[MAX_PATH_LEN + 20];
    snprintf(cmd, sizeof(cmd), "mkdir -p '%s'", dir);
    int ret = system(cmd);
    
    free(path_copy);
    return (ret == 0) ? 0 : -1;
}

int apply_create_event(dfr_event_t *event) {
    create_parent_dirs(event->path);
    
    FILE *fp = fopen(event->path, "wb");
    if (!fp) {
        syslog(LOG_ERR, "Failed to create file %s: %s", event->path, strerror(errno));
        return -1;
    }
    
    if (event->data && event->data_len > 0) {
        size_t written = fwrite(event->data, 1, event->data_len, fp);
        if (written != event->data_len) {
            syslog(LOG_ERR, "Failed to write file content for %s", event->path);
            fclose(fp);
            return -1;
        }
    }
    
    fclose(fp);
    
    // Set permissions
    if (chmod(event->path, event->permissions) != 0) {
        syslog(LOG_WARNING, "Failed to set permissions for %s", event->path);
    }
    
    log_event("Created file: %s (size: %u bytes)", event->path, event->file_size);
    return 0;
}

int apply_modify_event(dfr_event_t *event) {
    FILE *fp = fopen(event->path, "wb");
    if (!fp) {
        syslog(LOG_ERR, "Failed to open file %s for modification: %s", 
               event->path, strerror(errno));
        return -1;
    }
    
    if (event->data && event->data_len > 0) {
        size_t written = fwrite(event->data, 1, event->data_len, fp);
        if (written != event->data_len) {
            syslog(LOG_ERR, "Failed to write modified content for %s", event->path);
            fclose(fp);
            return -1;
        }
    }
    
    fclose(fp);
    
    // Set permissions
    if (chmod(event->path, event->permissions) != 0) {
        syslog(LOG_WARNING, "Failed to set permissions for %s", event->path);
    }
    
    log_event("Modified file: %s (size: %u bytes)", event->path, event->file_size);
    return 0;
}

int apply_delete_event(dfr_event_t *event) {
    struct stat st;
    
    if (stat(event->path, &st) != 0) {
        // File doesn't exist, nothing to do
        return 0;
    }
    
    if (S_ISDIR(st.st_mode)) {
        if (rmdir(event->path) != 0) {
            syslog(LOG_ERR, "Failed to remove directory %s: %s", 
                   event->path, strerror(errno));
            return -1;
        }
    } else {
        if (unlink(event->path) != 0) {
            syslog(LOG_ERR, "Failed to delete file %s: %s", 
                   event->path, strerror(errno));
            return -1;
        }
    }
    
    log_event("Deleted: %s", event->path);
    return 0;
}

int apply_event(dfr_event_t *event) {
    if (!event) {
        return -1;
    }
    
    syslog(LOG_INFO, "Applying event: type=%d, path=%s, node=%s", 
           event->type, event->path, event->node_id);
    
    int result = 0;
    
    switch (event->type) {
        case EVENT_CREATE:
            result = apply_create_event(event);
            break;
            
        case EVENT_MODIFY:
            result = apply_modify_event(event);
            break;
            
        case EVENT_DELETE:
            result = apply_delete_event(event);
            break;
            
        case EVENT_MOVE:
            // For simplicity, treat move as delete + create
            syslog(LOG_INFO, "Move event treated as delete for %s", event->path);
            result = apply_delete_event(event);
            break;
            
        default:
            syslog(LOG_WARNING, "Unknown event type: %d", event->type);
            result = -1;
    }
    
    return result;
}