#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <syslog.h>
#include "dfr.h"

typedef struct event_node {
    dfr_event_t event;
    struct event_node *next;
} event_node_t;

static event_node_t *queue_head = NULL;
static event_node_t *queue_tail = NULL;
static pthread_mutex_t queue_mutex = PTHREAD_MUTEX_INITIALIZER;
static int queue_size = 0;

int init_event_queue(void) {
    pthread_mutex_init(&queue_mutex, NULL);
    queue_head = NULL;
    queue_tail = NULL;
    queue_size = 0;
    syslog(LOG_INFO, "Event queue initialized");
    return 0;
}

int enqueue_event(dfr_event_t *event) {
    pthread_mutex_lock(&queue_mutex);
    
    if (queue_size >= EVENT_QUEUE_SIZE) {
        pthread_mutex_unlock(&queue_mutex);
        syslog(LOG_WARNING, "Event queue is full");
        return -1;
    }
    
    event_node_t *node = malloc(sizeof(event_node_t));
    if (!node) {
        pthread_mutex_unlock(&queue_mutex);
        syslog(LOG_ERR, "Failed to allocate memory for event node");
        return -1;
    }
    
    memcpy(&node->event, event, sizeof(dfr_event_t));
    node->next = NULL;
    
    if (queue_tail) {
        queue_tail->next = node;
    } else {
        queue_head = node;
    }
    queue_tail = node;
    queue_size++;
    
    pthread_mutex_unlock(&queue_mutex);
    return 0;
}

dfr_event_t* dequeue_event(void) {
    pthread_mutex_lock(&queue_mutex);
    
    if (!queue_head) {
        pthread_mutex_unlock(&queue_mutex);
        return NULL;
    }
    
    event_node_t *node = queue_head;
    dfr_event_t *event = malloc(sizeof(dfr_event_t));
    
    if (!event) {
        pthread_mutex_unlock(&queue_mutex);
        return NULL;
    }
    
    memcpy(event, &node->event, sizeof(dfr_event_t));
    
    queue_head = node->next;
    if (!queue_head) {
        queue_tail = NULL;
    }
    queue_size--;
    
    free(node);
    pthread_mutex_unlock(&queue_mutex);
    
    return event;
}

int process_event_queue(dfr_config_t *config) {
    dfr_event_t *event;
    int processed = 0;
    
    while ((event = dequeue_event()) != NULL) {
        // Send to peers
        send_event_to_peers(event);
        
        // Log event
        log_event("Processing event: type=%d path=%s", event->type, event->path);
        
        // Free event data
        if (event->data) {
            free(event->data);
        }
        free(event);
        
        processed++;
    }
    
    return processed;
}

void cleanup_event_queue(void) {
    pthread_mutex_lock(&queue_mutex);
    
    event_node_t *current = queue_head;
    while (current) {
        event_node_t *next = current->next;
        if (current->event.data) {
            free(current->event.data);
        }
        free(current);
        current = next;
    }
    
    queue_head = NULL;
    queue_tail = NULL;
    queue_size = 0;
    
    pthread_mutex_unlock(&queue_mutex);
    pthread_mutex_destroy(&queue_mutex);
    
    syslog(LOG_INFO, "Event queue cleanup completed");
}