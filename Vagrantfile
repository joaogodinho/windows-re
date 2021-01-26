WINDOWS_BOX="gusztavvargadr/windows-10"
WINDOWS_RAM=4096
WINDOWS_CPU=2

ROUTER_BOX="bento/ubuntu-20.04"
ROUTER_RAM=4096
ROUTER_CPU=1

Vagrant.configure("2") do |config|
  config.vm.define "router" do |subconfig|
    subconfig.vm.box = ROUTER_BOX
    subconfig.vm.synced_folder "shared_folders/ubuntu", "/vagrant"
    subconfig.vm.provider "virtualbox" do |vb|
      vb.memory = ROUTER_RAM
      vb.cpus = ROUTER_CPU
      vb.customize ["modifyvm", :id, "--vrde", "off"]
      vb.customize ['modifyvm', :id, '--clipboard', 'bidirectional']
    end
    subconfig.vm.network "private_network", ip: "192.168.1.1", virtualbox__intnet: "rev_net"
    subconfig.vm.network "forwarded_port", guest: 443, host: 8443, host_ip: "127.0.0.1"

		subconfig.vm.provision "ansible" do |ansible|
    	ansible.playbook = "ansible/playbooks/router.yml"
      ansible.config_file = "ansible/ansible.cfg"
      ansible.groups = {
        "router" => ["default"]
      }
    end
  end
  config.vm.define "windows10" do |subconfig|
    subconfig.vm.box = WINDOWS_BOX
    subconfig.vm.synced_folder "shared_folders/windows", "/vagrant"
    subconfig.vm.provider "virtualbox" do |vb|
      vb.memory = WINDOWS_RAM
      vb.cpus = WINDOWS_CPU
      vb.customize ["modifyvm", :id, "--vram", "256"]
      vb.customize ["modifyvm", :id, "--vrde", "off"]
      vb.customize ["modifyvm", :id, "--accelerate3d", "on"]
      vb.customize ['modifyvm', :id, '--clipboard', 'guesttohost']

      # Work around to make folder RO
      vb.customize ["sharedfolder", "remove", :id, "--name", "vagrant"]
      vb.customize ["sharedfolder", "add", :id, "--name", "vagrant", "--hostpath", File.dirname(__FILE__) + "/shared_folders/windows", "--readonly"]
    end

    subconfig.vm.network "private_network", ip: "192.168.1.10", virtualbox__intnet: "rev_net"
  end
end
