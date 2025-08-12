import socket
import time
import logging
import argparse
import struct
import sys
import subprocess
import platform
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Constants for drone connection
DRONE_IP = "192.168.1.1"
DRONE_CMD_PORT = 3333
DRONE_VIDEO_PORT = 2224
HEARTBEAT_INTERVAL = 0.9  # seconds

# Commands from protocol document
CMD_APP_ACCESS = b"APP_ACCESS"
CMD_OPEN_STREAM = b"OPEN_RT_STREAM"
CMD_CLOSE_STREAM = b"CLOSE_RT_STREAM"
CMD_HEARTBEAT = b"HEARTBEAT"

def network_test():
    """Test basic network connectivity to the drone"""
    logger.info("=== TESTING NETWORK CONNECTIVITY ===")

    # Test 1: Ping the drone
    logger.info(f"Test 1: Pinging drone at {DRONE_IP}...")
    try:
        import subprocess
        # Use ping with timeout of 1 second, 4 packets
        result = subprocess.run(['ping', '-n', '4', '-w', '1000', DRONE_IP], 
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if "Reply from" in result.stdout:
            logger.info("✅ Ping successful - drone is reachable")
            logger.info(f"Ping results:\n{result.stdout}")
        else:
            logger.error(f"❌ Ping failed - drone not reachable at {DRONE_IP}")
            logger.error(f"Ping results:\n{result.stdout}")
            return False
    except Exception as e:
        logger.error(f"❌ Ping test failed with error: {e}")
        return False

    return True

def command_port_test():
    """Test connection to the command port"""
    logger.info("=== TESTING COMMAND PORT CONNECTION ===")
    
    try:
        # Create command socket
        cmd_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        cmd_socket.settimeout(5.0)
        
        logger.info(f"Connecting to {DRONE_IP}:{DRONE_CMD_PORT}...")
        cmd_socket.connect((DRONE_IP, DRONE_CMD_PORT))
        logger.info("✅ Connection established")
        
        # Try sending APP_ACCESS command
        logger.info(f"Sending APP_ACCESS command...")
        cmd_socket.send(CMD_APP_ACCESS)
        
        # Try to receive a response if protocol specifies one
        try:
            cmd_socket.settimeout(2.0)
            response, _ = cmd_socket.recvfrom(1024)
            logger.info(f"Received response: {response}")
        except socket.timeout:
            logger.info("No response received (this might be normal)")
        
        # Try sending a heartbeat
        logger.info(f"Sending HEARTBEAT command...")
        cmd_socket.send(CMD_HEARTBEAT)
        
        cmd_socket.close()
        logger.info("✅ Command port test passed")
        return True
        
    except Exception as e:
        logger.error(f"❌ Command port test failed: {e}")
        return False

def check_firewall():
    """Check if Windows Firewall might be blocking the video port"""
    logger.info("=== CHECKING FIREWALL SETTINGS ===")
    
    if platform.system() != "Windows":
        logger.info("Firewall check only supported on Windows")
        return
        
    try:
        # Check if the UDP port is allowed in Windows Firewall
        result = subprocess.run(['netsh', 'advfirewall', 'firewall', 'show', 'rule', 
                               'name=all', 'dir=in'], 
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        if str(DRONE_VIDEO_PORT) in result.stdout:
            logger.info(f"✅ Found firewall rules that might allow port {DRONE_VIDEO_PORT}")
        else:
            logger.warning(f"⚠️ No specific firewall rules found for port {DRONE_VIDEO_PORT}")
            logger.info("Consider adding a firewall rule with this command:")
            logger.info(f"netsh advfirewall firewall add rule name=\"Drone Video\" dir=in action=allow protocol=UDP localport={DRONE_VIDEO_PORT}")
    except Exception as e:
        logger.error(f"Error checking firewall: {e}")

def test_alternative_video_ports():
    """Test alternative video ports that might work"""
    logger.info("=== TESTING ALTERNATIVE VIDEO PORTS ===")
    
    alt_ports = [11111, 8889, 8890, 8554, 6038, 7070]
    cmd_socket = None
    
    try:
        # Create command socket
        cmd_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        cmd_socket.settimeout(5.0)
        cmd_socket.connect((DRONE_IP, DRONE_CMD_PORT))
        
        # Send APP_ACCESS
        cmd_socket.send(CMD_APP_ACCESS)
        time.sleep(0.5)
        
        for port in alt_ports:
            logger.info(f"Testing alternative video port: {port}")
            video_socket = None
            
            try:
                # Create video socket on alternative port
                video_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                video_socket.bind(('0.0.0.0', port))
                video_socket.settimeout(3.0)
                
                # Send OPEN_RT_STREAM
                cmd_socket.send(CMD_OPEN_STREAM)
                
                # Try to receive data
                logger.info(f"Waiting for data on port {port}...")
                try:
                    # Send heartbeat
                    cmd_socket.send(CMD_HEARTBEAT)
                    data, addr = video_socket.recvfrom(65535)
                    logger.info(f"✅ Received data on port {port}! Size: {len(data)} bytes")
                    logger.info(f"Consider updating DRONE_VIDEO_PORT to {port} in the script")
                    return True
                except socket.timeout:
                    logger.info(f"No data received on port {port}")
                
                # Close stream
                cmd_socket.send(CMD_CLOSE_STREAM)
                
            except Exception as e:
                logger.info(f"Error testing port {port}: {e}")
            finally:
                if video_socket:
                    video_socket.close()
                    
        logger.info("No alternative ports worked")
        return False
    except Exception as e:
        logger.error(f"Error in alternative port test: {e}")
        return False
    finally:
        if cmd_socket:
            cmd_socket.close()

def check_network_adapters():
    """Check network adapters for potential issues"""
    logger.info("=== CHECKING NETWORK ADAPTERS ===")
    
    try:
        if platform.system() == "Windows":
            result = subprocess.run(['ipconfig', '/all'], 
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            
            # Find the adapter connected to the drone network
            lines = result.stdout.splitlines()
            current_adapter = None
            found_drone_adapter = False
            
            for line in lines:
                if "adapter" in line.lower() and ":" in line:
                    current_adapter = line
                
                # Check if this adapter has the drone's IP subnet
                if "IPv4 Address" in line and "192.168.1." in line:
                    logger.info(f"Found adapter with correct subnet: {current_adapter}")
                    logger.info(line)
                    found_drone_adapter = True
                    
                    # Check if this adapter has multiple IP addresses which might cause issues
                    if "192.168.1.1" in line:
                        logger.warning("⚠️ Your computer has the same IP as the drone! This will cause conflicts")
            
            if not found_drone_adapter:
                logger.warning("⚠️ No network adapter found in the drone's subnet (192.168.1.x)")
                logger.info("Make sure you're connected to the drone's WiFi network")
        else:
            logger.info("Network adapter check only supported on Windows")
            
    except Exception as e:
        logger.error(f"Error checking network adapters: {e}")

def wifi_info():
    """Get information about the current WiFi connection"""
    logger.info("=== CHECKING WIFI CONNECTION ===")
    try:
        import subprocess
        result = subprocess.run(['netsh', 'wlan', 'show', 'interfaces'], 
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        # Parse output to find SSID and signal strength
        ssid = "Unknown"
        signal = "Unknown"
        
        for line in result.stdout.splitlines():
            if "SSID" in line and "BSSID" not in line:
                ssid = line.split(':', 1)[1].strip() if ':' in line else "Unknown"
            if "Signal" in line:
                signal = line.split(':', 1)[1].strip() if ':' in line else "Unknown"
        
        logger.info(f"WiFi Network: {ssid}")
        logger.info(f"Signal Strength: {signal}")
        
        # Check if connected to drone network
        if "APEX" in ssid.upper() or "G-149" in ssid.upper() or "DRONE" in ssid.upper():
            logger.info("✅ Connected to what appears to be the drone network")
        else:
            logger.warning("⚠️ Not connected to an obvious drone network")
            logger.warning("Make sure you're connected to the drone's WiFi network")
    except Exception as e:
        logger.error(f"Error checking WiFi: {e}")

def main():
    parser = argparse.ArgumentParser(description="Drone Communication Diagnostic Tool")
    parser.add_argument("--test", choices=["all", "network", "command", "video", "advanced"],
                        default="all", help="Test to run")
    parser.add_argument("--ip", help="Override drone IP address")
    parser.add_argument("--video-port", type=int, help="Override video port")
    parser.add_argument("--cmd-port", type=int, help="Override command port")
    args = parser.parse_args()
    
    # Override defaults if specified
    global DRONE_IP, DRONE_VIDEO_PORT, DRONE_CMD_PORT
    if args.ip:
        DRONE_IP = args.ip
        logger.info(f"Using custom drone IP: {DRONE_IP}")
    if args.video_port:
        DRONE_VIDEO_PORT = args.video_port
        logger.info(f"Using custom video port: {DRONE_VIDEO_PORT}")
    if args.cmd_port:
        DRONE_CMD_PORT = args.cmd_port
        logger.info(f"Using custom command port: {DRONE_CMD_PORT}")
    
    logger.info("=== DRONE COMMUNICATION DIAGNOSTIC TOOL ===")
    logger.info(f"Running on Python {sys.version}")
    logger.info(f"Using drone IP: {DRONE_IP}, CMD port: {DRONE_CMD_PORT}, Video port: {DRONE_VIDEO_PORT}")
    
    # Always check WiFi
    wifi_info()
    
    # Check network adapters
    check_network_adapters()
    
    if args.test == "all" or args.test == "network":
        if not network_test():
            logger.error("Network test failed - fix connection issues before continuing")
            return
    
    if args.test == "all" or args.test == "command":
        if not command_port_test():
            logger.error("Command port test failed - check drone power and connectivity")
            return
    
    if args.test == "all" or args.test == "video":
        if not test_alternative_video_ports():
            logger.error("Video port test failed - check firewall settings and drone connectivity")
            
            # Additional diagnostics for video failure
            check_firewall()
            
            # Only run advanced tests if specifically requested or in advanced mode
            if args.test == "advanced":
                logger.info("Trying alternative video ports...")
                test_alternative_video_ports()
            else:
                logger.info("For more detailed diagnostics, run with --test advanced")
            
            return
    
    if args.test == "advanced":
        logger.info("Running advanced diagnostics...")
        check_firewall()
        test_alternative_video_ports()

if __name__ == "__main__":
    main()
