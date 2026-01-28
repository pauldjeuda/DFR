CC=gcc
CFLAGS=-Wall -Iinclude
SRC=src/dfr_main.c src/inotify.c src/replicator.c src/network.c src/event_queue.c src/logger.c src/config.c src/control.c
OUT=dfrd

all:
	$(CC) $(CFLAGS) $(SRC) -o $(OUT)

install:
	mkdir -p $(DESTDIR)/usr/bin
	cp $(OUT) $(DESTDIR)/usr/bin/

	mkdir -p $(DESTDIR)/etc/dfr
	cp config/dfr.conf $(DESTDIR)/etc/dfr/

	mkdir -p $(DESTDIR)/lib/systemd/system
	cp systemd/dfr.service $(DESTDIR)/lib/systemd/system/

clean:
	rm -f $(OUT)
