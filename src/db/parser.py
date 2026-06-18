"""Stateless parser for Senedd XML files, isolating Pandas and Numpy conversions."""
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Any


def _to_native(val):
    """Convert a pandas/numpy scalar to a native Python type (None for NaN)."""
    if pd.isna(val):
        return None
    if hasattr(val, "item"):  # numpy type
        return val.item()
    return val


def _to_datetime(val):
    """Coerce a value to a native datetime, or None on failure/NaN."""
    if pd.isna(val) or val is None:
        return None
    try:
        return pd.to_datetime(val).to_pydatetime()
    except Exception:
        return None


def _col(row, name):
    """Read a column from a row, tolerating columns absent in this file's schema."""
    if name in row.index:
        return _to_native(row[name])
    return None


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


def parse_votes_xml(
    xml_file: Path,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Parse a Senedd Plenary *Votes* XML export.

    The source is long-format — one row per (motion × member). Motion-level
    fields repeat on every row, so votes are deduplicated by ``Contribution_ID``
    (the natural key, which links back to the transcript's motion contribution).

    Returns:
        (meeting_data, votes_list, vote_records_list, members_list)
        - ``votes_list``: one dict per distinct motion.
        - ``vote_records_list``: one dict per member-vote (result is the raw
          source string: For | Against | Abstain | DidNotVote).
        - ``members_list``: distinct members for defensive upsert (a member may
          vote without ever having spoken in an ingested transcript).
    """
    try:
        df = pd.read_xml(xml_file)
    except ValueError:
        # pandas raises (rather than returning empty) when the root has no vote
        # child nodes — treat a contentless export as "nothing to ingest".
        return {}, [], [], []

    if df.empty:
        return {}, [], [], []

    first = df.iloc[0]
    meeting_data = {
        "meeting_id": _to_native(first["Meeting_ID"]),
        "assembly": _to_native(first["Assembly"]),
        "meeting_date": _to_datetime(first["MeetingDate"]),
        "meeting_type": "plenary-votes",
    }

    votes: Dict[Any, Dict[str, Any]] = {}
    vote_records: List[Dict[str, Any]] = []
    members: Dict[Any, Dict[str, Any]] = {}

    for _, row in df.iterrows():
        contribution_id = _col(row, "Contribution_ID")
        if contribution_id is None:
            continue

        if contribution_id not in votes:
            votes[contribution_id] = {
                "contribution_id": contribution_id,
                "meeting_id": _col(row, "Meeting_ID"),
                "assembly": _col(row, "Assembly"),
                "agenda_item_id": _col(row, "Agenda_Item_ID"),
                "agenda_item_english": _col(row, "Agenda_item_english"),
                "agenda_item_welsh": _col(row, "Agenda_item_welsh"),
                "vote_name_english": _col(row, "Vote_Name_English"),
                "vote_name_welsh": _col(row, "Vote_Name_Welsh"),
                "total_for": _col(row, "VotesTotalFor"),
                "total_against": _col(row, "VotesTotalAgainst"),
                "total_abstain": _col(row, "VotesTotalAbstain"),
                "result_english": _col(row, "Vote_Result_English"),
                "result_welsh": _col(row, "Vote_Result_Welsh"),
            }

        member_id = _col(row, "Member_Id")
        result = _col(row, "Results_Result")
        if member_id is not None and result is not None:
            vote_records.append({
                "contribution_id": contribution_id,
                "member_id": member_id,
                "result": result,
            })
            members.setdefault(member_id, {
                "member_id": member_id,
                "name_english": _col(row, "Member_name_English") or "",
                "sort_code": _col(row, "Member_Sortcode"),
            })

    return meeting_data, list(votes.values()), vote_records, list(members.values())


def parse_qnr_xml(
    xml_file: Path,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Parse a Senedd Plenary *QNR* export (written questions/answers not reached).

    The feed has no ``Contribution_ID`` and no explicit Q↔A link, so:
      - ``order_index`` is the parser-assigned document position (the source's
        ``Contribution_Order_ID`` is not unique and cannot key rows).
      - Q&A are paired positionally: each ``QNR`` row opens a new pair; the
        ``ANR`` row(s) that follow inherit it. ``pair_id`` is a deterministic
        ``"<meeting_id>-<n>"`` string so re-ingest is idempotent.
    Text fields are returned RAW (still double-escaped HTML); cleaning happens at
    ingest, mirroring the transcript's raw/clean separation.

    Returns:
        (meeting_data, written_list, members_list)
    """
    try:
        df = pd.read_xml(xml_file)
    except ValueError:
        return {}, [], []

    if df.empty:
        return {}, [], []

    first = df.iloc[0]
    meeting_id = _to_native(first["Meeting_ID"])
    meeting_data = {
        "meeting_id": meeting_id,
        "assembly": _to_native(first["Assembly"]),
        "meeting_date": _to_datetime(first["MeetingDate"]),
        "meeting_type": "plenary-qnr",
    }

    written: List[Dict[str, Any]] = []
    members: Dict[Any, Dict[str, Any]] = {}
    question_seq = 0
    current_pair = f"{meeting_id}-0"  # fallback for an answer before any question

    for order_index, (_, row) in enumerate(df.iterrows()):
        ctype = (_col(row, "contribution_type") or "").strip().upper()
        is_question = ctype == "QNR"
        if is_question:
            question_seq += 1
            current_pair = f"{meeting_id}-{question_seq}"

        member_id = _col(row, "Member_Id")
        # Member_Id arrives as float (the column has NaN on answer rows); coerce
        # to int so it satisfies the integer FK.
        if member_id is not None:
            member_id = int(member_id)
        written.append({
            "meeting_id": meeting_id,
            "assembly": _to_native(first["Assembly"]),
            "order_index": order_index,
            "agenda_item_id": _col(row, "Agenda_Item_ID"),
            "agenda_item_english": _col(row, "Agenda_item_english"),
            "agenda_item_welsh": _col(row, "Agenda_item_welsh"),
            "qa_role": "question" if is_question else "answer",
            "pair_id": current_pair,
            "speaker_id": member_id,
            "speaker_name_english": _col(row, "Member_name_English"),
            "speaker_job_title_english": _col(row, "Member_job_title_English"),
            "speaker_job_title_welsh": _col(row, "Member_job_title_Welsh"),
            "raw_verbatim": _col(row, "contribution_verbatim"),
            "raw_translated": _col(row, "contribution_translated"),
        })

        # Questions carry an identifiable asker; upsert defensively.
        if member_id is not None:
            members.setdefault(member_id, {
                "member_id": member_id,
                "name_english": _col(row, "Member_name_English") or "",
                "biography_english": _col(row, "Member_biog_English"),
                "biography_welsh": _col(row, "Member_biog_Welsh"),
                "sort_code": _col(row, "Member_sortcode"),
            })

    return meeting_data, written, list(members.values())
