import base64
import hashlib
import json
import tempfile
import unittest

try:
    from fastapi.testclient import TestClient
    from thermal_executor.app import create_app
    HTTP_AVAILABLE=True
except ImportError:
    HTTP_AVAILABLE=False

from test_core import SNAPSHOT,STEP

@unittest.skipUnless(HTTP_AVAILABLE,"Install executor[test] to run HTTP composition checks")
class WorkerHttp(unittest.TestCase):
    def test_authenticated_multipart_and_json_share_frozen_identity(self):
        with tempfile.TemporaryDirectory() as root:
            app=create_app(root=root,executable="",token="test-only",start_worker=False)
            with TestClient(app) as client:
                self.assertEqual(client.get("/health").status_code,401)
                client.headers["Authorization"]="Bearer test-only"
                first=client.post("/jobs",data={"snapshot":json.dumps(SNAPSHOT),"idempotencyKey":"same","sha256":hashlib.sha256(STEP).hexdigest()},files={"geometry":("a.step",STEP,"application/octet-stream")})
                self.assertEqual(first.status_code,200,first.text)
                second=client.post("/jobs",json={"snapshot":SNAPSHOT,"idempotencyKey":"same","geometry":{"filename":"a.step","sha256":hashlib.sha256(STEP).hexdigest(),"contentBase64":base64.b64encode(STEP).decode()}})
                self.assertEqual(second.status_code,200,second.text)
                self.assertEqual(first.json()["id"],second.json()["id"])
                jid=first.json()["id"]
                self.assertEqual(client.post("/jobs/"+jid+"/cancel",json={"confirm":False}).status_code,422)
                self.assertEqual(client.post("/jobs/"+jid+"/cancel",json={"confirm":True}).json()["status"],"cancelled")
                self.assertEqual(client.get("/jobs/"+jid+"/artifacts/state.json").status_code,404)

if __name__=="__main__":
    unittest.main()
