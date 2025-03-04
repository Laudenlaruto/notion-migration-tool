from notion_client.helpers import collect_paginated_api
import os
import logging
from notion_client import Client
from pprint import pprint
from notion_client import APIErrorCode, APIResponseError


def getOwnerFromOldDb(ownerId):
    try:
        owner = notion.pages.retrieve(ownerId)
        # print(owner)
        # Check if the owner has an email
        if len(owner.get("properties").get("lien_person (Pour Rollup)").get("people")) == 0:
            print("no people")
            return None
        if owner.get("properties").get("lien_person (Pour Rollup)").get("people")[0].get(
                "person") is None:
            print("no email")
            return None
        else:
            ownerEmail = owner.get("properties").get("lien_person (Pour Rollup)").get("people")[0].get(
                "person").get("email")
        return ownerEmail
    except APIResponseError as error:
        if error.code == APIErrorCode.ObjectNotFound:
            logging.error(error)
        else:
            # Other error handling code
            logging.error(error)


try:
    notion = Client(
        auth="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")
    # Tech notes : ab4ac06a5b6b45ed951df04307a90663
    # Doc tech 7c572848e4f04761b659c8f14c6d516e
    # Test db 1818f3776f4f80158a6ac3fd054fc9c5
    # All theodoers e2fa07c0424b473f994f176a636bec2a
    # Sicariotes (old) b970458a757d41238e5d892713d2981f
    technotes = collect_paginated_api(
        notion.databases.query, database_id="ab4ac06a5b6b45ed951df04307a90663"
    )
    for note in technotes:
        # Print note name
        print(note.get("properties").get("Name").get(
            "title")[0].get("plain_text"))
        currentExperts = note.get("properties").get("Experts").get("people")
        expertEmails = []
        for expert in currentExperts:
            if expert.get("person") is None:
                print("no email")
            else:
                expertEmails.append(expert.get("person").get("email"))
                print(expert.get("person").get("email"))
        # Update page properties`New Experts`
        newExpertsId = []
        for email in expertEmails:
            expert = notion.databases.query(
                **{
                    "database_id": "e2fa07c0424b473f994f176a636bec2a",
                    "filter": {
                        "property": "⚙️ Email",
                        "rich_text": {
                            "contains": email,
                        },
                    },
                }
            )
            if expert.get("results") == [] or len(expert.get("results")) > 1:
                print("Expert not found")
            else:
                print("Expert found")
                expert_id = expert.get("results")[0].get("id")
                newExpertsId.append({"id": expert_id})
        # Add new experts to the note
        if len(newExpertsId) == 0:
            print("no experts")
        else:
            notion.pages.update(
                page_id=note.get("id"),
                properties={
                    "New Experts": {"relation": newExpertsId},
                })
            print("Experts updated")

        # if len(currentOwner) == 0:
        #     print("no owner " + note.get("properties").get("Name").get("title")
        #           [0].get("plain_text"))
        # else:
        #     ownerEmail = getOwnerFromOldDb(currentOwner[0].get("id"))
        #     print(ownerEmail)
        #     if ownerEmail is not None:
        #         owner = notion.databases.query(
        #             **{
        #                 "database_id": "e2fa07c0424b473f994f176a636bec2a",
        #                 "filter": {
        #                     "property": "⚙️ Email",
        #                     "rich_text": {
        #                         "contains": ownerEmail,
        #                     },
        #                 },
        #             }
        #         )
        #         if owner.get("results") == [] or len(owner.get("results")) > 1:
        #             print("Owner not found")
        #         else:
        #             print("Owner found")
        #             owner_id = owner.get("results")[0].get("id")
        #             # Update page properties`New Owner`
        #             notion.pages.update(
        #                 page_id=note.get("id"),
        #                 properties={
        #                     "New Owner": {"relation": [{"id": owner_id}]},
        #                 }
        #             )
        #             print("Owner updated")

except APIResponseError as error:
    if error.code == APIErrorCode.ObjectNotFound:
        logging.error(error)
    else:
        # Other error handling code
        logging.error(error)
