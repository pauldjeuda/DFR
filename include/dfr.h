#ifndef DFR_H
#define DFR_H

#include <stdint.h>
#include <time.h>
#include <sys/types.h>
#include <sys/stat.h>

#define MAX_PATH_LEN 4096
#define MAX_NODES 64
#define MAX_WATCH_DIRS 256
#define EVENT_QUEUE_SIZE 1024
#define CONTROL_SOCKET "/var/run/dfr/control.sock"
#define LOG_FILE "/var/log/dfr/dfr.log"

// Event types
typedef enum {
    EVENT_CREATE = 1,
    EVENT_MODIFY = 2,
    EVENT_DELETE = 3,
    EVENT_MOVE = 4
} event_type_t;

// Event structure
typedef struct {
    event_type_t type;
    char path[MAX_PATH_LEN];
    uint64_t timestamp;
    uint32_t file_size;
    mode_t permissions;
    char node_id[64];
    uint8_t *data;
    size_t data_len;
} dfr_event_t;

// Configuration structure
typedef struct {
    char watch_dir[MAX_PATH_LEN];
    char node_id[64];
    int network_port;
    char peers[MAX_NODES][256];
    int peer_count;
    int debug_mode;
    int control_port;
} dfr_config_t;

// Node structure
typedef struct {
    char node_id[64];
    char ip_address[64];
    int port;
    time_t last_seen;
    int connected;
} dfr_node_t;

// Function declarations

// Config
int load_config(dfr_config_t *config);

// Logger
int init_logger(dfr_config_t *config);
void log_event(const char *format, ...);
void cleanup_logger(void);

// Event queue
int init_event_queue(void);
int enqueue_event(dfr_event_t *event);
dfr_event_t* dequeue_event(void);
void cleanup_event_queue(void);
int process_event_queue(dfr_config_t *config);

// Inotify
int init_inotify(dfr_config_t *config);
void* inotify_worker(void *arg);
void cleanup_inotify(void);

// Network
int init_network(dfr_config_t *config);
void* network_worker(void *arg);
int send_event_to_peers(dfr_event_t *event);
void cleanup_network(void);

// Replicator
int apply_event(dfr_event_t *event);

// Conflict manager
int resolve_conflict(dfr_event_t *local, dfr_event_t *remote);

// Control interface
void init_control(dfr_config_t *config);
void* control_worker(void *arg);
void cleanup_control(void);

#endif // DFR_H