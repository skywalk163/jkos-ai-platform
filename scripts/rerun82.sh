cd /data/dsh/code-jkos
/data/dsh/venv-test/bin/python -m pytest \
  tests/test_mcp_server.py::TestMCPServerRoutes::test_sse_stream \
  tests/test_mcp_server.py::TestMCPServerRoutes::test_sse_stream_heartbeat \
  tests/test_storage.py::TestStorageFileOperations::test_download_file_s3error \
  tests/test_storage.py::TestStorageFileOperations::test_delete_file_s3error \
  tests/test_storage.py::TestMinIOImportFallback::test_minio_import_fallback \
  tests/test_workflow_m1.py::test_d1_code_review_end_to_end \
  --tb=long -q --no-cov > /data/dsh/test-fail.log 2>&1
echo "EXIT=$?"
cat /data/dsh/test-fail.log
