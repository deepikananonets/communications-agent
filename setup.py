#!/usr/bin/env python3
"""
Setup script for Patient Responsibility Memo Agent

This script helps set up the environment and configuration.
"""

import os
import sys
import subprocess

def install_requirements():
    """Install required packages."""
    print("Installing required packages...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        print("✅ Requirements installed successfully")
        return True
    except subprocess.CalledProcessError:
        print("❌ Failed to install requirements")
        return False

def check_python_version():
    """Check Python version compatibility."""
    if sys.version_info < (3, 7):
        print("❌ Python 3.7 or higher is required")
        return False
    print(f"✅ Python {sys.version_info.major}.{sys.version_info.minor} is compatible")
    return True

def setup_configuration():
    """Guide user through configuration setup."""
    print("\n=== Configuration Setup ===")
    
    config_file = "config.py"
    if not os.path.exists(config_file):
        print(f"❌ Configuration file {config_file} not found")
        return False
    
    print("Please update the following in config.py:")
    print("1. ZAPIER_WEBHOOK_URL - Replace XXXXXXX with your actual Zapier hook IDs")
    print("2. AMD_CONFIG credentials if different from defaults")
    print("3. PROCESSING_CONFIG parameters if needed")
    
    return True

def run_tests():
    """Run connection tests."""
    print("\n=== Running Connection Tests ===")
    try:
        result = subprocess.run([sys.executable, "test_connections.py"], capture_output=True, text=True)
        print(result.stdout)
        if result.stderr:
            print("Errors:", result.stderr)
        return result.returncode == 0
    except Exception as e:
        print(f"❌ Failed to run tests: {e}")
        return False

def main():
    """Main setup function."""
    print("=== Patient Responsibility Memo Agent Setup ===\n")
    
    steps = [
        ("Checking Python version", check_python_version),
        ("Installing requirements", install_requirements),
        ("Setting up configuration", setup_configuration)
    ]
    
    for step_name, step_func in steps:
        print(f"\n{step_name}...")
        if not step_func():
            print(f"❌ Setup failed at: {step_name}")
            return False
    
    print("\n✅ Setup completed successfully!")
    print("\nNext steps:")
    print("1. Update config.py with your Zapier webhook URL")
    print("2. Run: python test_connections.py")
    print("3. Run: python patient_responsibility_agent.py")
    
    # Optionally run tests
    response = input("\nWould you like to run connection tests now? (y/n): ")
    if response.lower().startswith('y'):
        run_tests()
    
    return True

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
