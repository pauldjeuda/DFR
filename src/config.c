#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <syslog.h>
#include "dfr.h"

#define CONFIG_FILE "/etc/dfr/dfr.conf"

static void trim_whitespace(char *str) {
    char *end;
    while (*str == ' ' || *str == '\t' || *str == '\n' || *str == '\r') {
        str++;
    }
    
    end = str + strlen(str) - 1;
    while (end > str && (*end == ' ' || *end == '\t' || *end == '\n' || *end == '\r')) {
        end--;
    }
    *(end + 1) = '\0';
}

int load_config(dfr_config_t *config) {
    FILE *fp = fopen(CONFIG_FILE, "r");
    
    if (!fp) {
        syslog(LOG_WARNING, "Config file not found, using defaults");
        
        // Set defaults
        strncpy(config->watch_dir, "/var/dfr/sync", sizeof(config->watch_dir));
        gethostname(config->node_id, sizeof(config->node_id));
        config->network_port = 9876;
        config->control_port = 9877;
        config->peer_count = 0;
        config->debug_mode = 0;
        
        return 0;
    }
    
    // Initialize config
    memset(config, 0, sizeof(dfr_config_t));
    config->network_port = 9876;
    config->control_port = 9877;
    config->debug_mode = 0;
    
    char line[1024];
    while (fgets(line, sizeof(line), fp)) {
        trim_whitespace(line);
        
        // Skip comments and empty lines
        if (line[0] == '#' || line[0] == '\0') {
            continue;
        }
        
        char key[256], value[768];
        if (sscanf(line, "%255[^=]=%767[^\n]", key, value) == 2) {
            trim_whitespace(key);
            trim_whitespace(value);
            
            if (strcmp(key, "watch_dir") == 0) {
                strncpy(config->watch_dir, value, sizeof(config->watch_dir) - 1);
            }
            else if (strcmp(key, "node_id") == 0) {
                strncpy(config->node_id, value, sizeof(config->node_id) - 1);
            }
            else if (strcmp(key, "network_port") == 0) {
                config->network_port = atoi(value);
            }
            else if (strcmp(key, "control_port") == 0) {
                config->control_port = atoi(value);
            }
            else if (strcmp(key, "debug_mode") == 0) {
                config->debug_mode = (strcmp(value, "yes") == 0 || strcmp(value, "1") == 0);
            }
            else if (strcmp(key, "peer") == 0) {
                if (config->peer_count < MAX_NODES) {
                    strncpy(config->peers[config->peer_count], value, 255);
                    config->peer_count++;
                }
            }
        }
    }
    
    fclose(fp);
    
    // Set node_id from hostname if not configured
    if (config->node_id[0] == '\0') {
        gethostname(config->node_id, sizeof(config->node_id));
    }
    
    // Validate watch_dir
    if (config->watch_dir[0] == '\0') {
        strncpy(config->watch_dir, "/var/dfr/sync", sizeof(config->watch_dir));
    }
    
    syslog(LOG_INFO, "Configuration loaded: node_id=%s, watch_dir=%s, port=%d, peers=%d",
           config->node_id, config->watch_dir, config->network_port, config->peer_count);
    
    return 0;
}