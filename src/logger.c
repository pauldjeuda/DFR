#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include <time.h>
#include <pthread.h>
#include <syslog.h>
#include <sys/stat.h>
#include "dfr.h"

static FILE *log_fp = NULL;
static pthread_mutex_t log_mutex = PTHREAD_MUTEX_INITIALIZER;

int init_logger(dfr_config_t *config) {
    // Create log directory if it doesn't exist
    system("mkdir -p /var/log/dfr");
    
    log_fp = fopen(LOG_FILE, "a");
    if (!log_fp) {
        syslog(LOG_ERR, "Failed to open log file: %s", LOG_FILE);
        return -1;
    }
    
    pthread_mutex_init(&log_mutex, NULL);
    
    log_event("DFR Logger initialized");
    return 0;
}

void log_event(const char *format, ...) {
    if (!log_fp) {
        return;
    }
    
    pthread_mutex_lock(&log_mutex);
    
    time_t now = time(NULL);
    struct tm *tm_info = localtime(&now);
    char timestamp[64];
    strftime(timestamp, sizeof(timestamp), "%Y-%m-%d %H:%M:%S", tm_info);
    
    fprintf(log_fp, "[%s] ", timestamp);
    
    va_list args;
    va_start(args, format);
    vfprintf(log_fp, format, args);
    va_end(args);
    
    fprintf(log_fp, "\n");
    fflush(log_fp);
    
    pthread_mutex_unlock(&log_mutex);
}

void cleanup_logger(void) {
    if (log_fp) {
        log_event("DFR Logger shutting down");
        fclose(log_fp);
        log_fp = NULL;
    }
    pthread_mutex_destroy(&log_mutex);
}