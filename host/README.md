# host

Configuration that lives outside Docker on the server.

| File | What |
|---|---|
| `etc/fstab` | the two USB data disks and the mergerfs pool `/mnt/disk*` -> `/data` (`category.create=mfs`, `minfreespace=50G`) |
| `etc/samba/smb.conf` | the `[lab7x]` share of `/data` |
| `etc/docker/daemon.json` | DNS servers for Docker |
| `etc/netplan/50-cloud-init.yaml` | wired DHCP on `eno1` |
| `etc/systemd/system/pihole-pw.service`, `home/pihole-pw.sh` | runs `pihole setpassword` at boot |
| `crontab.txt` | the user crontab: subtitle pre-warming every 30 minutes and the nightly repo sync |
| `packages.txt` | `apt-mark showmanual` - packages installed by hand |

Also installed on the host: Tailscale for remote access to the LAN-only apps, mergerfs
and Samba.
