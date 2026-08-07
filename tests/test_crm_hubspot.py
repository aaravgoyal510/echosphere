import pytest
from unittest.mock import MagicMock, patch
from dialogue_manager.models import QualificationData, QualificationValue
from integrations.crm.hubspot import HubSpotCRMAdapter
from integrations.db_manager import DBManager

@pytest.fixture
def mock_db_manager():
    db = MagicMock(spec=DBManager)
    db.use_sqlite = True
    return db

@pytest.fixture
def hubspot_adapter(mock_db_manager):
    with patch.dict("os.environ", {"HUBSPOT_ACCESS_TOKEN": "test_hubspot_token"}):
        adapter = HubSpotCRMAdapter(mock_db_manager)
        return adapter

@patch("httpx.post")
@patch("httpx.patch")
def test_hubspot_upsert_lead_new(mock_patch, mock_post, hubspot_adapter):
    """Asserts that upsert_lead performs a search first, and calls POST to create if not found."""
    # 1. Mock search response (empty results -> contact doesn't exist)
    mock_search_res = MagicMock()
    mock_search_res.status_code = 200
    mock_search_res.json.return_value = {"results": []}
    
    # 2. Mock create response (returning contact ID 12345)
    mock_create_res = MagicMock()
    mock_create_res.status_code = 201
    mock_create_res.json.return_value = {
        "id": "12345",
        "properties": {
            "firstname": "John",
            "lastname": "Doe",
            "phone": "+123456",
            "email": "john@doe.com",
            "company": "DoeCorp",
            "echosphere_seats": "25"
        },
        "createdAt": "2026-08-07T00:00:00Z",
        "updatedAt": "2026-08-07T00:00:00Z"
    }
    
    mock_post.side_effect = [mock_search_res, mock_create_res]
    
    qual = QualificationData(
        team_size=QualificationValue(value=25, last_updated_turn=0, source="stated")
    )
    
    # Mock fallback adapter inside
    hubspot_adapter.mock_adapter = MagicMock()
    
    lead = hubspot_adapter.upsert_lead(
        phone="+123456",
        name="John Doe",
        email="john@doe.com",
        company="DoeCorp",
        qualification=qual
    )
    
    # Assertions
    assert lead.lead_id == "12345"
    assert lead.email == "john@doe.com"
    assert lead.qualification.team_size.value == 25
    
    # Check that search-before-create was performed
    assert mock_post.call_count == 2
    # Verify search API call structure
    first_call_args = mock_post.call_args_list[0]
    assert "/search" in first_call_args[0][0]
    search_payload = first_call_args[1]["json"]
    assert search_payload["filterGroups"][0]["filters"][0]["value"] == "john@doe.com"
    
    # Verify create API call structure
    second_call_args = mock_post.call_args_list[1]
    create_payload = second_call_args[1]["json"]
    assert create_payload["properties"]["firstname"] == "John"
    assert create_payload["properties"]["lastname"] == "Doe"
    assert create_payload["properties"]["echosphere_seats"] == "25"


@patch("httpx.post")
@patch("httpx.patch")
def test_hubspot_upsert_lead_existing(mock_patch, mock_post, hubspot_adapter):
    """Asserts that upsert_lead performs a search, and calls PATCH to update if contact exists."""
    # 1. Mock search response (found existing contact ID 12345)
    mock_search_res = MagicMock()
    mock_search_res.status_code = 200
    mock_search_res.json.return_value = {
        "results": [
            {
                "id": "12345",
                "properties": {
                    "firstname": "John",
                    "lastname": "Doe",
                    "phone": "+123456",
                    "email": "john@doe.com",
                    "company": "DoeCorp",
                    "echosphere_seats": "25"
                }
            }
        ]
    }
    mock_post.return_value = mock_search_res
    
    # 2. Mock update response (returning updated properties)
    mock_update_res = MagicMock()
    mock_update_res.status_code = 200
    mock_update_res.json.return_value = {
        "id": "12345",
        "properties": {
            "firstname": "John",
            "lastname": "Doe",
            "phone": "+123456",
            "email": "john@doe.com",
            "company": "DoeCorp",
            "echosphere_seats": "45"
        },
        "createdAt": "2026-08-07T00:00:00Z",
        "updatedAt": "2026-08-07T00:00:00Z"
    }
    mock_patch.return_value = mock_update_res
    
    qual = QualificationData(
        team_size=QualificationValue(value=45, last_updated_turn=0, source="stated")
    )
    
    hubspot_adapter.mock_adapter = MagicMock()
    
    lead = hubspot_adapter.upsert_lead(
        phone="+123456",
        name="John Doe",
        email="john@doe.com",
        company="DoeCorp",
        qualification=qual
    )
    
    assert lead.lead_id == "12345"
    assert lead.qualification.team_size.value == 45
    
    # Ensure search was performed and patch was called instead of post for creation
    assert mock_post.call_count == 1
    assert mock_patch.call_count == 1
    
    patch_url = mock_patch.call_args[0][0]
    assert "/12345" in patch_url
    patch_payload = mock_patch.call_args[1]["json"]
    assert patch_payload["properties"]["echosphere_seats"] == "45"


@patch("httpx.post")
@patch("httpx.patch")
def test_hubspot_update_qualification(mock_patch, mock_post, hubspot_adapter):
    """Asserts that update_lead_qualification patches the correct contact property fields."""
    mock_update_res = MagicMock()
    mock_update_res.status_code = 200
    mock_update_res.json.return_value = {
        "id": "12345",
        "properties": {
            "firstname": "John",
            "lastname": "Doe",
            "echosphere_seats": "45",
            "echosphere_competitor": "HubSpot"
        }
    }
    mock_patch.return_value = mock_update_res
    
    qual = QualificationData(
        team_size=QualificationValue(value=45, last_updated_turn=0, source="stated"),
        current_solution=QualificationValue(value="HubSpot", last_updated_turn=0, source="stated")
    )
    
    hubspot_adapter.mock_adapter = MagicMock()
    
    lead = hubspot_adapter.update_lead_qualification("12345", qual)
    
    assert lead.lead_id == "12345"
    assert lead.qualification.team_size.value == 45
    assert lead.qualification.current_solution.value == "HubSpot"
    
    assert mock_patch.call_count == 1
    patch_payload = mock_patch.call_args[1]["json"]
    assert patch_payload["properties"]["echosphere_seats"] == "45"
    assert patch_payload["properties"]["echosphere_competitor"] == "HubSpot"
