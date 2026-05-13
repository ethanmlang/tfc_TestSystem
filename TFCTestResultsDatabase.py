from pathlib import Path

import yaml

class TFCTestResultsDatabase:
    def _extract_vnv_metadata(self, test_name: str) -> dict[str, str]:
        parts = Path(test_name).parts
        marker = ("test", "vnv", "cases")

        for i in range(len(parts) - len(marker) + 1):
            if parts[i:i + len(marker)] != marker:
                continue
            if i + 4 >= len(parts):
                break

            return {
                "vnv_type": parts[i + 3],
                "case_type": parts[i + 4],
                "case_name": parts[i + 5] if i + 5 < len(parts) else "",
                "input_file": parts[i + 6] if i + 6 < len(parts) else "",
            }

        return {
            "vnv_type": "",
            "case_type": "",
            "case_name": Path(test_name).stem,
            "input_file": Path(test_name).name,
        }

    def writeResultsDatabase(self):
        """
        Writes the results database to a designated file as a YAML
        formatted file.
        """
        db_filename = self.test_results_database_outputfile_

        db_file = open(db_filename, "w")

        database = []

        # Now we loop over the tests
        test_objects = self.tests_
        for test_object in test_objects:
            test_data = {}
            test_data["name"] = test_object.name_
            test_data.update(self._extract_vnv_metadata(test_object.name_))
            test_data["requirements"] = test_object.requirements_
            test_data["passed"] = test_object.passed_
            test_data["annotations"] = test_object.test_result_annotation_
            test_data["test_doc_page"] = test_object.test_doc_page_
            test_data["tagged_results"] = test_object.tagged_results_
            database.append(test_data)

        yaml.dump(database, db_file, sort_keys=False)

        db_file.close()
