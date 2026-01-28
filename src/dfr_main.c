#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <syslog.h>
#include <sys/stat.h>
#include <pthread.h>
#include "dfr.h"

volatile sig_atomic_t running = 1;

void signal_handler(int sig) {
    if (sig == SIGTERM || sig == SIGINT) {
        syslog(LOG_INFO, "Received termination signal");
        running = 0;
    }
}

void daemonize() {
    pid_t pid = fork();
    
    if (pid < 0) {
        exit(EXIT_FAILURE);
    }
    
    if (pid > 0) {
        exit(EXIT_SUCCESS);
    }
    
    if (setsid() < 0) {
        exit(EXIT_FAILURE);
    }
    
    signal(SIGCHLD, SIG_IGN);
    signal(SIGHUP, SIG_IGN);
    
    pid = fork();
    
    if (pid < 0) {
        exit(EXIT_FAILURE);
    }
    
    if (pid > 0) {
        exit(EXIT_SUCCESS);
    }
    
    umask(0);
    chdir("/");
    
    close(STDIN_FILENO);
    close(STDOUT_FILENO);
    close(STDERR_FILENO);
}

int main(int argc, char *argv[]) {
    dfr_config_t config;
    pthread_t inotify_thread, network_thread, control_thread;
    
    // Open syslog
    openlog("dfrd", LOG_PID | LOG_CONS, LOG_DAEMON);
    syslog(LOG_INFO, "DFR Daemon starting...");
    
    memset(&config, 0, sizeof(config));
    
    // Load configuration
    if (load_config(&config) != 0) {
        syslog(LOG_ERR, "Failed to load configuration");
        return EXIT_FAILURE;
    }
    
    // Daemonize if not in debug mode
    if (!config.debug_mode) {
        daemonize();
    }
    
    // Setup signal handlers
    signal(SIGTERM, signal_handler);
    signal(SIGINT, signal_handler);
    
    // Initialize components
    if (init_logger(&config) != 0) {
        syslog(LOG_ERR, "Failed to initialize logger");
        return EXIT_FAILURE;
    }
    
    if (init_event_queue() != 0) {
        syslog(LOG_ERR, "Failed to initialize event queue");
        return EXIT_FAILURE;
    }
    
    if (init_inotify(&config) != 0) {
        syslog(LOG_ERR, "Failed to initialize inotify");
        return EXIT_FAILURE;
    }
    
    if (init_network(&config) != 0) {
        syslog(LOG_ERR, "Failed to initialize network");
        return EXIT_FAILURE;
    }
    
    if (&config != NULL) {
        syslog(LOG_ERR, "Failed to initialize control interface");
        return EXIT_FAILURE;
    }
    
    // Start threads
    pthread_create(&inotify_thread, NULL, inotify_worker, &config);
    pthread_create(&network_thread, NULL, network_worker, &config);
    pthread_create(&control_thread, NULL, control_worker, &config);
    
    syslog(LOG_INFO, "DFR Daemon started successfully");
    
    // Main loop
    while (running) {
        process_event_queue(&config);
        usleep(100000); // 100ms
    }
    
    // Cleanup
    syslog(LOG_INFO, "Shutting down DFR Daemon...");
    
    pthread_cancel(inotify_thread);
    pthread_cancel(network_thread);
    pthread_cancel(control_thread);
    
    pthread_join(inotify_thread, NULL);
    pthread_join(network_thread, NULL);
    pthread_join(control_thread, NULL);
    
    cleanup_inotify();
    cleanup_network();
    cleanup_control();
    cleanup_event_queue();
    cleanup_logger();
    
    syslog(LOG_INFO, "DFR Daemon stopped");
    closelog();
    
    return EXIT_SUCCESS;
}