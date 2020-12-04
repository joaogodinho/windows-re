# Windows RE
How to setup a Windows environment for reverse engineering.

# Requirements
- Vagrant
- VirtualBox
- Ansible

# Setup
1. Check the RAM and CPU vars in the Vagrantfile fit your system
2. Run `vagrant up` and wait for everything to boot
3. Open the Windows VM, add any custom settings (e.g.: keyboard layout, performance settings) or tools (e.g.: Flare-VM), shutdown the VM and take a snapshot

Regular internet access is not longer needed for the Windows VM, and now we'll configure the Windows to route all traffic via the Ubuntu VM.

1. In Windows adapter options
    1. disable the first adapter (should be named `Ethernet`).
    2. in the second adapter (should be name `Ethernet2`) modify the IPv4 properties and set:
        - Subnet mask: 255.255.255.0
        - Default gateway: 192.168.1.1
        - Default DNS: 8.8.8.8
2. Shutdown the machine and take a snapshot (this snapshot should be the one to rollback after doing some analysis)
3. In VirtualBox Network settings for the Windows VM, disable Adapter 1

__Note that if you manually shutdown the windows VM and run `vagrant up` again, it will re-enable the Adapter 1 Network, which is why we also disable it in Windows itself__

# Tips
- Disable effects on windows: Run `sysdm.cpl`, **Advanced -> Performance Settings -> Adjust for best performance**
- Install Flare-VM [1] (ideally before cutting internet access)

[1]: https://github.com/fireeye/flare-vm#installation-install-script
