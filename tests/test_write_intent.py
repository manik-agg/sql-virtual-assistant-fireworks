"""Write-request detection (the deterministic backup to the model's reason=write_attempt).

Both directions matter: a missed write request gets the wrong refusal wording, and a false positive would call a legitimate
read question a write attempt. The lists below were written before the implementation.
"""

import pytest

from src.intent import looks_like_write_request

WRITES = [
    "Delete all loans",
    "Delete every loan with status D.",
    "Drop the client table.",
    "Please drop table loan",
    "Update loan 5000 set amount = 0",
    "Insert a new client named Eva",
    "Alter the account table to add a column",
    "Truncate the trans table",
    "Set the amount of loan 5000 to zero.",
    "Change the district of account 1 to Prague.",
    "Modify client 7's gender to F",
    "Edit the status of loan 12 to B",
    "Overwrite every balance with 0",
    "Replace 'A' with 'B' in the loan status column",
    "Please make every loan amount zero",
    "Make all balances null",
    "Add a new client named Eva.",
    "Add a row to the card table",
    "Mark loan 12 as paid",
    "Reset all balances to 0",
    "Can you delete all loans?",
    "Could you please update the loan amounts to zero?",
    "I want you to remove client 5",
    "Rename the district table to region",
    "Create a new table called archive",
    "Grant admin access to user bob",
    "Merge accounts 1 and 2",
    "Remove all loans with status D",
    "Go ahead and drop the order table",
    "Wipe all transactions.",
    "Erase client 17 from the system",
    "Append a new card for disposition 77",
    "Please change loan 4959 amount to 100",
    "Set loan status to 'A' where loan_id = 5",
    "Set every account's frequency to weekly",
    "Make loan 4959 amount 0 please",
    "Could you remove all failed loans from the database?",
    "Edit row 5 of the client table",
]

READS = [
    "How many loans are there?",
    "Create a ranking of districts by average salary.",
    "Create a table of loans per region.",
    "Create a summary of loans by status",
    "Remove duplicates and list the distinct regions.",
    "Remove the cancelled loans from the results",
    "Remove all duplicate clients from the list.",
    "Change in total loan amount between 1996 and 1997?",
    "Change in the number of accounts per year from 1993 to 1997",
    "Update me on how many loans are in default.",
    "Add up the amounts of all transactions in 1996.",
    "Add the loan amounts for each client.",
    "Set of districts with more than 100000 inhabitants?",
    "Set aside the cancelled ones; how many loans remain?",
    "Replace nothing, just count loans",
    "Which accounts had a status change to D?",
    "Make a list of clients born in 1970.",
    "Mark the loans that are in debt",
    "How many loans were deleted?",
    "Which accounts were updated in 1996?",
    "How can I delete a loan?",
    "Show clients whose gender was changed to F",
    "What is the total amount of loans that were dropped?",
    # parameter / refinement phrases: they change the QUERY, not the data
    "Change the region to Prague",
    "Set the threshold to 100000 and count the big loans",
    "Change the sort to descending order and list loans",
    "Change over time of the number of loans per year",
    "Set the order by amount descending and show the top 5",
    "Add the transaction amounts together per account",
    "Create a bar chart of accounts per region",
    "Reset the sort and list clients by gender",
    "Mark my words: how many loans have status A?",
    "Make the five biggest loans first",
]


@pytest.mark.parametrize("question", WRITES)
def test_write_requests_are_recognised(question):
    assert looks_like_write_request(question), question


@pytest.mark.parametrize("question", READS)
def test_read_questions_that_look_similar_are_not_flagged(question):
    assert not looks_like_write_request(question), question


def test_empty_and_odd_input_never_raises():
    for text in ("", "   ", "???", "\n", "☃"):
        assert looks_like_write_request(text) is False
