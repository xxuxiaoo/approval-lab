import argparse
import ipaddress

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Approval Lab · 审核模型实验室")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        loopback = ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        loopback = args.host == "localhost"
    if not loopback:
        parser.error("This application stores API keys and only supports a loopback host.")
    uvicorn.run("approval_lab.app:app", host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
