from pathlib import Path


def _resource_block(template: str, logical_id: str, next_logical_id: str) -> str:
    start = template.index(f"  {logical_id}:")
    end = template.index(f"  {next_logical_id}:", start)
    return template[start:end]


def test_office_core_can_read_related_company_and_office_records() -> None:
    template = (Path(__file__).parents[1] / "template.yaml").read_text(encoding="utf-8")
    block = _resource_block(template, "OfficeCoreFunction", "AssignmentFunction")

    assert "DynamoDBReadPolicy: { TableName: !Ref CompaniesTable }" in block
    assert "DynamoDBReadPolicy: { TableName: !Ref OfficesTable }" in block
