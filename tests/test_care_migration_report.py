import unittest
from unittest.mock import patch

from crm import caremigration


class CareMigrationReportTest(unittest.TestCase):
    def test_consolidates_server_batches_and_trainer_residual(self):
        complete={"state":"complete","counts":{"applied":2,"already":3,"unmatched":0,"ambiguous":0,"errors":0},
                  "unmatched":[],"ambiguous":[],"errors":[]}
        partial={"state":"partial","counts":{"applied":1,"already":0,"unmatched":1,"ambiguous":0,"errors":0},
                 "unmatched":[{"source_id":"sz:1","name":"Коля"}],"ambiguous":[],"errors":[]}
        modules=["targetedverifiedcarebatch","targetedverifiedcarehistory","targetedverifiedcareearly",
                 "targetedverifiedlatesales","targetedverifiedcaregaps"]
        patches=[patch(f"crm.{name}.report",return_value=partial if name==modules[-1] else complete) for name in modules]
        with patches[0],patches[1],patches[2],patches[3],patches[4],patch(
                "crm.targetedtrainerresidual.report",return_value={"state":"partial","added":4,"already":5,"unresolved":[{}]}):
            result=caremigration.report(None)
        self.assertEqual("partial",result["state"])
        self.assertEqual({"applied":9,"already":12,"unmatched":1,"ambiguous":0,"errors":0},result["totals"])
        self.assertEqual("Коля",result["unresolved"][0]["name"])


if __name__=="__main__":unittest.main()
