FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl python3 libicu74 libssl3t64 libgssapi-krb5-2 zlib1g && rm -rf /var/lib/apt/lists/*
COPY install-runtime.sh prepare-wine-fonts.py /usr/local/lib/playlite-vm/
RUN bash /usr/local/lib/playlite-vm/install-runtime.sh
COPY guest/ /app/
COPY backup.py /app/backup.py
USER ubuntu
ENV HOME=/home/ubuntu
RUN python3 -c "import sys; sys.path.insert(0,'/app'); import install_cli; install_cli.install(status=print)"
USER root
RUN chmod -R a+rX /home/ubuntu/.local/share/playlite/wine-steam-auto-crack
ENV HOME=/work/home WINEPREFIX=/work/prefix PLAYLITE_AUTOCRACK_JOBS=/work/jobs PLAYLITE_AUTOCRACK_CONTAINER=1
USER 1000:1000
ENTRYPOINT ["xvfb-run", "-a", "python3", "/app/container_entry.py"]
