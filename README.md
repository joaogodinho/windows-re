# Windows RE
The purpose of this project is to facilitate the deployment of infrastructure to do reversing and analysis of malware.

We use Vagrant and Ansible to launch a Windows 10 and an Ubuntu 20.04 machine.

The Ubuntu (named `router`) serves as a router for the Windows machine.
It dumps all traffic and strips TLS before forwarding it through Tor.
To facilitate traffic analysis, Malcolm \[1\] is deployed on the `router` machine.

# Requirements
- Vagrant
- VirtualBox
- Ansible

# Setup
1. Check the RAM and CPU vars in the Vagrantfile fit your system
2. Run `ansible-galaxy install -r ansible/requirements.yml`
3. Run `vagrant up` and wait for everything to boot
4. Open the Windows VM, add any custom settings (e.g.: keyboard layout, performance settings) or tools (e.g.: Flare-VM), shutdown the VM and take a snapshot

Regular internet access is not longer needed for the Windows VM, and now we'll configure the Windows to route all traffic via the Ubuntu VM.

1. In Windows adapter options
    1. disable the first adapter (should be named `Ethernet`).
    2. in the second adapter (should be name `Ethernet2`) modify the IPv4 properties and set:
        - Subnet mask: 255.255.255.0
        - Default gateway: 192.168.1.1
        - Default DNS: 8.8.8.8
2. Shutdown the machine and take a snapshot (this snapshot should be the one to rollback after doing some analysis)
3. In VirtualBox Network settings for the Windows VM, disable Adapter 1
4. In the `shared_folders/ubuntu` directory a certificate was placed from PolarProxy, copy to `shared_folders/windows` and install the certificate as a root CA

__Note that if you manually shutdown the windows VM and run `vagrant up` again, it will re-enable the Adapter 1 Network, which is why we also disable it in Windows itself__

# Malcolm

To access malcolm, head to `https://localhost:8443`, the default credentials are `root:root`. The Kibana interface is available at `/kibana`.


# Tips
- Disable effects on windows: Run `sysdm.cpl`, **Advanced -> Performance Settings -> Adjust for best performance**
- Install Flare-VM \[2\] (ideally before cutting internet access)
- If using Chrome and need to bypass the invalid certificate, type just type `thisisunsafe`
- To SSH into the `router` VM, run `vagrant ssh router`
- To wipe malcolm data, go to `/home/vagrant/malcolm/` and run `python3 scripts/control.py --wipe`, after the wipe, run `docker-compose up -d` again to restart everything

# References

\[1\]: https://github.com/cisagov/Malcolm
\[2\]: https://github.com/fireeye/flare-vm#installation-install-script
