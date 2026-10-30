import time
from smart_home import SmartHomeController

def main():
    print("=" * 60)
    print(" Philips Hue Bridge Pairing Helper")
    print("=" * 60)

    controller = SmartHomeController()
    bridge_ip = controller.hue.bridge_ip
    print(f"\n[1] Discovered Hue Bridge IP: {bridge_ip}")

    if controller.hue.is_paired:
        print(f"[✓] Already paired! API key: {controller.hue.api_key[:8]}...")
        lights = controller.hue.get_lights()
        print(f"[✓] Found {len(lights)} physical lights:")
        for lid, info in lights.items():
            print(f"    - ID {lid}: '{info.get('name')}' (on={info.get('state', {}).get('on')}, bri={info.get('state', {}).get('bri')})")
        return

    print("\n[2] Please press the large circular LINK BUTTON on top of your Hue Bridge.")
    print("    Waiting up to 45 seconds for button press...\n")

    for i in range(45, 0, -2):
        print(f"    Waiting... ({i}s remaining)", end="\r", flush=True)
        res = controller.pair_hue()
        if res.get("success"):
            print("\n\n" + "=" * 60)
            print(" [✓] PAIRING SUCCESSFUL!")
            print("=" * 60)
            print(f"API Key saved to 'hue_config.json'.")
            lights = controller.hue.get_lights()
            print(f"\nSuccessfully discovered {len(lights)} physical lights:")
            for lid, info in lights.items():
                print(f"  • Light '{info.get('name')}' (Hue ID: {lid})")
            print("\nYour agent is now connected to your real Philips Hue lights!")
            return
        time.sleep(2)

    print("\n\n[!] Timed out. Please make sure the bridge is powered on and try again.")

if __name__ == "__main__":
    main()
