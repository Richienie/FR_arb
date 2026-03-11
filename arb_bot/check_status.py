"""快速检查系统状态"""
import sys
from pathlib import Path
from datetime import datetime
import time
import json


def main():
    print("=" * 60)
    print("ARBITRAGE SCANNER STATUS CHECK")
    print("=" * 60)
    print()

    # 1. 检查数据文件
    dashboard_file = Path(__file__).parent / "dashboard_data.json"

    status = "OK"
    issues = []

    print("[1] Data File Check")
    if not dashboard_file.exists():
        print("    Status: [X] MISSING")
        print(f"    File: {dashboard_file}")
        status = "ERROR"
        issues.append("dashboard_data.json does not exist")
    else:
        mtime = dashboard_file.stat().st_mtime
        age = time.time() - mtime

        print(f"    Status: {'[OK] EXISTS' if age < 60 else '[!] STALE'}")
        print(f"    File: {dashboard_file}")
        print(f"    Last modified: {datetime.fromtimestamp(mtime)}")
        print(f"    Age: {age:.1f} seconds")

        if age > 60:
            status = "WARNING"
            issues.append(f"Data file is stale (age: {age/60:.1f} minutes)")

        # 检查内容
        try:
            with open(dashboard_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if 'last_update' in data:
                data_age = time.time() - data['last_update']
                print(f"    Data timestamp: {datetime.fromtimestamp(data['last_update'])}")
                print(f"    Data age: {data_age:.1f} seconds")

                if data_age > 60:
                    status = "WARNING"
                    issues.append(f"Data content is old (age: {data_age/60:.1f} minutes)")

            # 检查是否有Lighter数据
            if 'store' in data:
                lighter_count = sum(1 for assets in data['store'].values()
                                   if 'Lighter' in assets)
                print(f"    Lighter symbols: {lighter_count}")
        except Exception as e:
            print(f"    [!] Error reading file: {e}")
            status = "WARNING"
            issues.append(f"Cannot read data file: {e}")

    print()
    print("[2] Expected Data Flow")
    print("    Lighter API → (poll 15s) → Scanner")
    print("    Scanner → (write 5s) → dashboard_data.json")
    print("    JSON file → (read 10s) → Dashboard")
    print("    Max delay: ~30 seconds")

    print()
    print("=" * 60)
    print(f"OVERALL STATUS: {status}")
    print("=" * 60)

    if issues:
        print("\nIssues found:")
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}")
        print("\nSolution:")
        print("  1. Make sure main.py is running:")
        print("     python main.py")
        print("  2. Wait 30 seconds for data to refresh")
        print("  3. Run this check again")
    else:
        print("\nAll systems operational!")
        print("Data is fresh and up-to-date.")

    print()

    # 返回退出码
    if status == "ERROR":
        sys.exit(1)
    elif status == "WARNING":
        sys.exit(2)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
