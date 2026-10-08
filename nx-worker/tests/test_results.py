from pathlib import Path
import tempfile
import unittest
from thermal_executor.results import solver_balance

class EnergyEvidence(unittest.TestCase):
    def test_prescribed_temperature_sinks_are_not_labelled_radiation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/"nx").mkdir()
            (root/"nx"/"MSGF").write_text("Heat flow into sinks = 2.000E+06\nHeat flow from non-fluid sinks = 2.000E+06\nHeat load into elements = 0.000E+00\nHeat load into sinks = 0.000E+00\nHeat flow from fluid sinks = 0.000E+00\nDeviation from heat balance =-1.736E+01\n")
            result=solver_balance(root,0)
            self.assertEqual(result["status"],"available")
            self.assertEqual(result["heatFlowIntoSinksW"],2)
            self.assertAlmostEqual(result["relativeResidual"],8.68e-6)
            self.assertNotIn("radiation_heat_loss_W",result)
            self.assertEqual(result["sourceArtifact"],"MSGF")

    def test_no_report_means_unavailable_not_zero_balance(self):
        with tempfile.TemporaryDirectory() as temporary:
            result=solver_balance(temporary,10)
            self.assertEqual(result["status"],"unavailable")
            self.assertNotIn("relativeResidual",result)

if __name__=="__main__":
    unittest.main()
