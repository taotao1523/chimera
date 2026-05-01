"""
Example: Fill a login form with human-like interaction.

Usage:
    python -m examples.form
"""

import asyncio
from chimera import Chimera


async def main():
    # Replace with a real login page
    LOGIN_URL = "https://httpbin.org/forms/post"
    USERNAME = "demo@example.com"
    PASSWORD = "secret123"

    async with Chimera.launch(LOGIN_URL) as c:
        print(f"Opened: {await c.title}")

        # Inspect form fields
        fields = await c.get_input_fields()
        print(f"Found {len(fields)} input fields:")
        for f in fields:
            print(f"  {f.get('tag')} name='{f.get('name')}' type='{f.get('type')}' "
                  f"label='{f.get('label')}'")

        # Fill the form
        await c.type_into("input[name='custname']", "John Doe")
        await c.type_into("input[name='custtel']", "555-0123")
        await c.type_into("input[name='custemail']", USERNAME)

        # Choose a pizza size (radio button)
        await c.click("input[value='medium']")

        # Add toppings
        await c.click("input[value='bacon']")
        await c.click("input[value='cheese']")

        # Submit
        await c.click("button[type='submit']")

        await asyncio.sleep(1)
        print(f"Submitted. Current URL: {await c.url}")


if __name__ == "__main__":
    asyncio.run(main())
