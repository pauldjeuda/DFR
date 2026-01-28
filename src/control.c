#include <stdio.h>
#include "dfr.h"

void init_control(dfr_config_t *config) {
    if (config){
        printf("[CONTROL] Initialisation du module de contrôle\n");
    }
}

void* control_worker(void *arg) {
    printf("[CONTROL] Worker de contrôle en cours\n");
    return NULL;
}

void cleanup_control(void) {
    printf("[CONTROL] Nettoyage du module de contrôle\n");
}