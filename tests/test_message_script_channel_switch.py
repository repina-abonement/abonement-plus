from pathlib import Path
import unittest


class MessageScriptChannelSwitchTest(unittest.TestCase):
    def test_script_is_refreshed_after_messenger_switch_without_clearing_draft(self):
        source = (Path(__file__).parents[1] / "crm/static/comms.js").read_text()

        self.assertNotIn("Мессенджер изменился. Откройте скрипты заново", source)
        self.assertIn("if(currentChannel!==channel)", source)
        self.assertIn("candidate.key===item.key", source)
        self.assertIn("input.value=selectedItem.text", source)
        # The draft is only replaced after the replacement script was fetched and validated.
        self.assertLess(source.index("selectedItem=refreshed.items.find"), source.index("input.value=selectedItem.text"))


if __name__ == "__main__":
    unittest.main()
