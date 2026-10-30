import json
from ps5_media import PS5MediaController

def main():
    print("=" * 60)
    print(" PS5 & Samsung Frame TV Auto-Discovery & Pairing Helper")
    print("=" * 60)

    controller = PS5MediaController()
    print("\n[1] Scanning local network (192.168.86.x) for PS5 & Samsung Frame TV...")
    res = controller.discover_and_configure(action="discover")

    ps5_info = res.get("ps5", {})
    tv_info = res.get("samsung_frame_tv", {})

    print("\n--- PlayStation 5 ---")
    if ps5_info.get("ip"):
        print(f" [✓] Discovered PS5 at IP: {ps5_info['ip']} (State: {ps5_info.get('state')}, Name: {ps5_info.get('host_name')})")
        if ps5_info.get("running_app_name"):
            print(f"     Currently running: {ps5_info.get('running_app_name')} ({ps5_info.get('running_app_titleid')})")
    else:
        print(" [!] PS5 not detected yet on UDP port 9302.")
        print("     Ensure PS5 is on the same Wi-Fi/LAN and Rest Mode network wake is enabled.")

    print("\n--- Samsung Frame TV ---")
    if tv_info.get("ip"):
        print(f" [✓] Discovered Samsung Frame TV at IP: {tv_info['ip']} ({tv_info.get('name')} - {tv_info.get('model')})")
        print("\n[2] Initiating WebSocket pairing with Samsung Frame TV (Port 8002)...")
        print("    >>> Grab your Samsung TV remote and click 'Allow' when 'EtaiAgent' appears on screen! <<<")
        pair_res = controller.tv.send_keys(["KEY_RETURN"], delay_ms=100, pair_timeout=45.0)
        controller._save_config()
        if pair_res.get("success"):
            print(f" [✓] Paired with Samsung Frame TV! Token ({controller.tv.tv_token}) saved to 'ps5_tv_config.json'.")
        else:
            print(f" [!] Pairing status: {json.dumps(pair_res, indent=2)}")
    else:
        print(" [!] Samsung Frame TV not detected yet on port 8001/SSDP.")
        print("     Ensure the TV is powered on or in Art Mode on the same LAN subnet.")

    print("\n[3] For first-time PS5 app-launch pairing with playactor, run:")
    print("    npx --yes playactor login --ps5")
    print("=" * 60)

if __name__ == "__main__":
    main()
