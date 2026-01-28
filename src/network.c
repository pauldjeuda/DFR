#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <errno.h>
#include <syslog.h>
#include "dfr.h"

#define PROTOCOL_VERSION 1
#define MAGIC_NUMBER 0x44465231  // "DFR1"

typedef struct {
    uint32_t magic;
    uint16_t version;
    uint16_t event_type;
    uint64_t timestamp;
    uint32_t path_len;
    uint32_t data_len;
    uint32_t file_size;
    uint32_t permissions;
    char node_id[64];
} network_packet_t;

static int server_socket = -1;
static dfr_config_t *global_config = NULL;

int init_network(dfr_config_t *config) {
    global_config = config;
    
    server_socket = socket(AF_INET, SOCK_STREAM, 0);
    if (server_socket < 0) {
        syslog(LOG_ERR, "Failed to create server socket: %s", strerror(errno));
        return -1;
    }
    
    int opt = 1;
    setsockopt(server_socket, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    
    struct sockaddr_in server_addr;
    memset(&server_addr, 0, sizeof(server_addr));
    server_addr.sin_family = AF_INET;
    server_addr.sin_addr.s_addr = INADDR_ANY;
    server_addr.sin_port = htons(config->network_port);
    
    if (bind(server_socket, (struct sockaddr *)&server_addr, sizeof(server_addr)) < 0) {
        syslog(LOG_ERR, "Failed to bind server socket: %s", strerror(errno));
        close(server_socket);
        return -1;
    }
    
    if (listen(server_socket, 10) < 0) {
        syslog(LOG_ERR, "Failed to listen on server socket: %s", strerror(errno));
        close(server_socket);
        return -1;
    }
    
    syslog(LOG_INFO, "Network server listening on port %d", config->network_port);
    return 0;
}

int send_event_to_peer(const char *peer_addr, int peer_port, dfr_event_t *event) {
    int sock = socket(AF_INET, SOCK_STREAM, 0);
    if (sock < 0) {
        return -1;
    }
    
    struct sockaddr_in peer;
    memset(&peer, 0, sizeof(peer));
    peer.sin_family = AF_INET;
    peer.sin_port = htons(peer_port);
    inet_pton(AF_INET, peer_addr, &peer.sin_addr);
    
    if (connect(sock, (struct sockaddr *)&peer, sizeof(peer)) < 0) {
        close(sock);
        return -1;
    }
    
    // Build packet
    network_packet_t packet;
    packet.magic = htonl(MAGIC_NUMBER);
    packet.version = htons(PROTOCOL_VERSION);
    packet.event_type = htons(event->type);
    packet.timestamp = event->timestamp;
    packet.path_len = htonl(strlen(event->path));
    packet.data_len = htonl(event->data_len);
    packet.file_size = htonl(event->file_size);
    packet.permissions = htonl(event->permissions);
    strncpy(packet.node_id, event->node_id, sizeof(packet.node_id));
    
    // Send packet header
    if (send(sock, &packet, sizeof(packet), 0) < 0) {
        close(sock);
        return -1;
    }
    
    // Send path
    if (send(sock, event->path, strlen(event->path), 0) < 0) {
        close(sock);
        return -1;
    }
    
    // Send data if present
    if (event->data && event->data_len > 0) {
        if (send(sock, event->data, event->data_len, 0) < 0) {
            close(sock);
            return -1;
        }
    }
    
    close(sock);
    return 0;
}

int send_event_to_peers(dfr_event_t *event) {
    int sent = 0;
    
    for (int i = 0; i < global_config->peer_count; i++) {
        char peer_addr[256];
        int peer_port;
        
        // Parse peer string (format: "ip:port")
        if (sscanf(global_config->peers[i], "%[^:]:%d", peer_addr, &peer_port) == 2) {
            if (send_event_to_peer(peer_addr, peer_port, event) == 0) {
                sent++;
            }
        }
    }
    
    return sent;
}

void handle_client(int client_sock) {
    network_packet_t packet;
    
    // Receive packet header
    if (recv(client_sock, &packet, sizeof(packet), 0) != sizeof(packet)) {
        close(client_sock);
        return;
    }
    
    // Verify magic number and version
    if (ntohl(packet.magic) != MAGIC_NUMBER) {
        syslog(LOG_WARNING, "Invalid magic number received");
        close(client_sock);
        return;
    }
    
    // Build event
    dfr_event_t event;
    memset(&event, 0, sizeof(event));
    event.type = ntohs(packet.event_type);
    event.timestamp = packet.timestamp;
    event.file_size = ntohl(packet.file_size);
    event.permissions = ntohl(packet.permissions);
    strncpy(event.node_id, packet.node_id, sizeof(event.node_id));
    
    uint32_t path_len = ntohl(packet.path_len);
    uint32_t data_len = ntohl(packet.data_len);
    
    // Receive path
    if (path_len > 0 && path_len < MAX_PATH_LEN) {
        recv(client_sock, event.path, path_len, 0);
        event.path[path_len] = '\0';
    }
    
    // Receive data
    if (data_len > 0) {
        event.data = malloc(data_len);
        if (event.data) {
            event.data_len = recv(client_sock, event.data, data_len, 0);
        }
    }
    
    close(client_sock);
    
    // Apply event
    apply_event(&event);
    
    if (event.data) {
        free(event.data);
    }
}

void* network_worker(void *arg) {
    syslog(LOG_INFO, "Network worker thread started");
    
    while (1) {
        struct sockaddr_in client_addr;
        socklen_t client_len = sizeof(client_addr);
        
        int client_sock = accept(server_socket, (struct sockaddr *)&client_addr, &client_len);
        
        if (client_sock < 0) {
            if (errno == EINTR) continue;
            syslog(LOG_ERR, "Accept error: %s", strerror(errno));
            continue;
        }
        
        handle_client(client_sock);
    }
    
    return NULL;
}

void cleanup_network(void) {
    if (server_socket >= 0) {
        close(server_socket);
    }
    syslog(LOG_INFO, "Network cleanup completed");
}