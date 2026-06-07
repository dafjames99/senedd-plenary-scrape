"""Stateless parser for Senedd XML files, isolating Pandas and Numpy conversions."""
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Any

def parse_senedd_xml(xml_file: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Parse a Senedd plenary XML file.
    Returns: (meeting_data, members_list, contributions_list)
    All elements are converted to native Python types.
    """
    df = pd.read_xml(xml_file)
    
    # Helper to convert numpy types to python natives
    def to_native(val):
        if pd.isna(val):
            return None
        if hasattr(val, 'item'):  # numpy type
            return val.item()
        return val
    
    def to_datetime(val):
        if pd.isna(val) or val is None:
            return None
        try:
            return pd.to_datetime(val).to_pydatetime()
        except:
            return None


    #NOTE: Following block is predicated on download file name-convention.
    #TODO: Custom error-handling to trace-back failures relating to download name-convention changes.
    
    name_split = xml_file.name.split('_') # Predicated on the filename being, e.g., "260602_Plenary_Bilingual.xml"
    m_type = name_split[1].split('-')[0].strip().lower() # Remove " - xth Senedd" from "YYMMDD_Plenary - xth Senedd_Bilingual.xml" -> return "plenary"
    if m_type == "plenary" and name_split[2] == "Votes": # "260602_Plenary_Votes.xml" - not a transcript
        m_type = "plenary-votes"

    # Parse meeting details
    meeting_data = {
        'meeting_id': to_native(df.iloc[0]['Meeting_ID']),
        'assembly': to_native(df.iloc[0]['Assembly']),
        'meeting_date': to_datetime(df.iloc[0]['MeetingDate']),
        'meeting_type': m_type
    }
    
    # Parse members details
    members_list = []
    members_df = df[df['Member_Id'].notna()][[
        'Member_Id', 'Member_name_English', 'Member_job_title_English',
        'Member_job_title_Welsh', 'Member_biog_English', 'Member_biog_Welsh',
        'Member_Sortcode'
    ]].drop_duplicates(subset=['Member_Id'])
    
    for _, row in members_df.iterrows():
        members_list.append({
            'member_id': to_native(row['Member_Id']),
            'name_english': to_native(row['Member_name_English']) or '',
            'biography_english': to_native(row['Member_biog_English']),
            'biography_welsh': to_native(row['Member_biog_Welsh']),
            'sort_code': to_native(row['Member_Sortcode']),
        })
        
    # Parse contributions details
    contributions_list = []
    for _, row in df.iterrows():
        contributions_list.append({
            'contribution_id': to_native(row['Contribution_ID']),
            'meeting_id': to_native(row['Meeting_ID']),
            'assembly': to_native(row['Assembly']),
            'meeting_date': to_datetime(row['MeetingDate']),
            'contribution_order_id': to_native(row['Contribution_Order_ID']),
            'contribution_language': to_native(row['contribution_language']),
            'contribution_time': to_datetime(row['ContributionTime']),
            'contribution_spoken_seneddtv': to_native(row['contribution_spoken_seneddTv']),
            'contribution_translated_seneddtv': to_native(row['contribution_translated_seneddTv']),
            'agenda_item_id': to_native(row['Agenda_Item_ID']),
            'agenda_item_welsh': to_native(row['Agenda_item_welsh']),
            'agenda_item_english': to_native(row['Agenda_item_english']),
            'contribution_type': to_native(row['contribution_type']),
            'attendee_id': to_native(row['Attendee_Id']) if pd.notna(row['Attendee_Id']) else None,
            'member_id': to_native(row['Member_Id']) if pd.notna(row['Member_Id']) else None,
            'member_name_english': to_native(row['Member_name_English']),
            'member_biog_english': to_native(row['Member_biog_English']),
            'member_biog_welsh': to_native(row['Member_biog_Welsh']),
            'member_job_title_english': to_native(row['Member_job_title_English']),
            'member_job_title_welsh': to_native(row['Member_job_title_Welsh']),
            'member_sortcode': to_native(row['Member_Sortcode']),
            'contribution_english': to_native(row['Contribution_English']),
            'contribution_welsh': to_native(row['Contribution_Welsh']),
            'contribution_verbatim': to_native(row['contribution_verbatim']),
            'contribution_translated': to_native(row['contribution_translated']),
        })
        
    return meeting_data, members_list, contributions_list
