from notion_client.helpers import collect_paginated_api
import os
import logging
from notion_client import Client
from notion_client import APIErrorCode, APIResponseError

imagenum = 0
unsupported_blocks_removed = 0

MAX_CODE_BLOCK_LENGTH = 2000
MAX_BLOCKS_PER_PAGE = 100


def filter_unsupported_blocks(blocks):
    """
    Recursively filter out blocks of type 'unsupported' from the block list.
    Also filters unsupported blocks in nested children.
    Returns the filtered blocks and count of removed blocks.
    """
    global unsupported_blocks_removed

    if not isinstance(blocks, list):
        return blocks

    filtered_blocks = []

    for block in blocks:
        if not isinstance(block, dict):
            filtered_blocks.append(block)
            continue

        # Skip blocks with type='unsupported'
        if block.get("type") == "unsupported":
            unsupported_blocks_removed += 1
            continue

        # Process nested children recursively
        block_type = block.get("type")
        if block.get("has_children") and block_type and block_type in block:
            if "children" in block[block_type]:
                block[block_type]["children"] = filter_unsupported_blocks(
                    block[block_type]["children"])

        # Add the block to the filtered results
        filtered_blocks.append(block)

    return filtered_blocks


def get_all_children(block_id):
    children = collect_paginated_api(
        notion.blocks.children.list, block_id=block_id)
    index = 0
    for child in children:
        # Remove parent key to create unique new object
        child.pop('parent', None)
        child.pop('created_time', None)
        child.pop('created_by', None)
        child.pop('last_edited_by', None)
        child.pop('last_edited_time', None)
        # If the block is an image or an external link, replace it with a warning because it's not supported by the API **yet**
        type = child.get("type")
        if type == "image" or type == "external":
            global imagenum
            imagenum += 1
            children[index] = {"paragraph": {
                "rich_text": [
                    {"text": {"content": "⚠️ Go fetch the image from the original doc ⚠️"},
                     "annotations": {
                        "bold": True,
                        "italic": False,
                        "strikethrough": False,
                        "underline": True,
                        "code": False,
                        "color": "red"
                    }, }]}}
        # File blocks must have an external property defined
        elif type == "file" and (not child.get("file") or not child.get("file").get("external")):
            children[index] = {"paragraph": {
                "rich_text": [
                    {"text": {"content": "⚠️ Go fetch the file from the original doc ⚠️"},
                     "annotations": {
                        "bold": True,
                        "italic": False,
                        "strikethrough": False,
                        "underline": True,
                        "code": False,
                        "color": "red"
                    }, }]}}
        # The `link_preview` block can only be returned as part of a response. The API does not support creating or appending `link_preview` blocks.
        # If the block is a link preview or a link mention within a rich text, replace it with a text block
        if child.get(type).get("rich_text", False):
            index2 = 0
            for text in child.get(type).get("rich_text"):
                sub_type = text.get("type")
                if sub_type == "mention":
                    block_type = text.get("mention").get("type")
                    if block_type == "link_preview":
                        children[index][type]["rich_text"][index2] = {
                            "text": {"content": text.get("mention").get(block_type).get("url")}, }
                    elif block_type == "link_mention":
                        children[index][type]["rich_text"][index2] = {
                            "text": {"content": text.get("mention").get(block_type).get("href")}, }
                index2 += 1
        # If the block is a link preview or a link mention, replace it with a bookmark block
        if type == "link_preview":
            children[index] = {
                "object": "block",
                "type": "bookmark",
                "bookmark": {
                    "url": child.get("link_preview").get("url")
                }
            }
        elif type == "link_mention":
            children[index] = {
                "object": "block",
                "type": "bookmark",
                "bookmark": {
                    "url": child.get("link_mention").get("href")
                }
            }
        # Continue recursively if the block has children
        if child.get("has_children"):
            child[child.get("type")]["children"] = get_all_children(
                child.get("id"))
            # remove id after getting children, to force creation of new block
            child.pop('id', None)
        index += 1
    # Filter out any unsupported blocks
    global unsupported_blocks_removed
    unsupported_blocks_removed = 0
    children = filter_unsupported_blocks(children)
    if unsupported_blocks_removed > 0:
        logging.info(
            f"Removed {unsupported_blocks_removed} unsupported block(s)")
        print(f"Removed {unsupported_blocks_removed} unsupported block(s)")
    # Return all children without splitting (preparation will happen later)
    return children


def split_long_code_blocks(blocks):
    """
    Split code blocks that exceed MAX_CODE_BLOCK_LENGTH characters into multiple blocks.
    Returns a flat list of blocks with long code blocks split into multiple sequential blocks.
    """
    if not isinstance(blocks, list):
        return blocks

    result = []

    for block in blocks:
        if not isinstance(block, dict):
            result.append(block)
            continue

        # Handle code blocks with content exceeding the limit
        if block.get("type") == "code" and "code" in block:
            rich_text = block["code"].get("rich_text", [])
            if rich_text and len(rich_text) > 0:
                content = rich_text[0].get("text", {}).get("content", "")

                if len(content) > MAX_CODE_BLOCK_LENGTH:
                    language = block["code"].get("language", "plain text")

                    # Split content into chunks
                    chunks = [content[i:i+MAX_CODE_BLOCK_LENGTH]
                              for i in range(0, len(content), MAX_CODE_BLOCK_LENGTH)]

                    # Create a block for each chunk
                    for i, chunk in enumerate(chunks):
                        # Copy the original block for the first chunk
                        if i == 0:
                            new_block = dict(block)
                            new_block["code"]["rich_text"][0]["text"]["content"] = chunk
                        else:
                            # Create continuation blocks for remaining chunks
                            new_block = {
                                "type": "code",
                                "object": "block",
                                "code": {
                                    "rich_text": [{"type": "text", "text": {"content": chunk}}],
                                    "language": language
                                }
                            }
                        result.append(new_block)
                    continue  # Skip appending the original block

        # Process nested children recursively
        if block.get("has_children"):
            block_type = block.get("type")
            if block_type and block_type in block and "children" in block[block_type]:
                block[block_type]["children"] = split_long_code_blocks(
                    block[block_type]["children"])

        # Add the block to the result
        result.append(block)

    return result


def prepare_blocks_for_notion(blocks):
    """
    Prepare blocks for Notion API by handling validation constraints:
    - Splits code blocks exceeding MAX_CODE_BLOCK_LENGTH characters
    - Returns prepared blocks for initial page creation and excess blocks for later appending
    """
    if not isinstance(blocks, list):
        return blocks, []

    # First split any code blocks that exceed the character limit
    all_blocks = split_long_code_blocks(blocks)

    # Then separate blocks for initial page creation (up to MAX_BLOCKS_PER_PAGE)
    # from excess blocks that will be appended later
    initial_blocks = all_blocks[:MAX_BLOCKS_PER_PAGE]
    excess_blocks = all_blocks[MAX_BLOCKS_PER_PAGE:]

    return initial_blocks, excess_blocks


try:
    notion = Client(
        auth="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",)
    # Tech notes : ab4ac06a5b6b45ed951df04307a90663
    # Doc tech 7c572848e4f04761b659c8f14c6d516e
    # Test db 1818f3776f4f80158a6ac3fd054fc9c5
    # All theodoers e2fa07c0424b473f994f176a636bec2a
    doctech = collect_paginated_api(
        notion.databases.query, database_id="7c572848e4f04761b659c8f14c6d516e"
    )
    # List over every doctech
    for doc in doctech[34:]:
        # Get all first level children blocks
        all_blocks = get_all_children(
            doc.get("id"))
        # Create a page in the Test db wit the same name and blocks
        name = "".join([title.get("plain_text")
                       for title in doc.get("properties").get("Name").get("title")])
        print(name)
        # Print block and its index
        # for index, block in enumerate(all_blocks):
        #     print(index, block)

        label = doc.get("properties").get(
            "Type").get("select").get("name")

        # Get the person who created the doc
        person = doc.get("properties").get("Created By").get(
            "created_by").get("person", None)

        if person is not None:
            created_by = person.get("email")
            print("Searching "+created_by)
            # Find the theodoer in the related database
            owner = notion.databases.query(
                **{
                    "database_id": "e2fa07c0424b473f994f176a636bec2a",
                    "filter": {
                        "property": "⚙️ Email",
                        "rich_text": {
                            "contains": created_by,
                        },
                    },
                }
            )
            if owner.get("results") == [] or len(owner.get("results")) > 1:
                print("Owner not found")
            else:
                print("Owner found")
            owner_id = owner.get("results")[0].get("id")
            prop = {
                "Name": {"title": [{"text": {"content": name}}]},
                "Labels": {"select": {"name": label}},
                "Owner": {"relation": [{"id": owner_id}]},
                "Experts": {"relation": [{"id": owner_id}]},
            }
        else:
            print("Original owner left the company")
            # Create page without owner
            prop = {
                "Name": {"title": [{"text": {"content": name}}]},
                "Labels": {"select": {"name": label}},
            }

        total_blocks = len(all_blocks)
        print(f"Count of blocks: {total_blocks}")
        # Prepare blocks for Notion - handle code block length limits and nested blocks
        initial_blocks, excess_blocks = prepare_blocks_for_notion(all_blocks)
        print(
            f"Prepared {len(initial_blocks)} initial blocks with {len(excess_blocks)} excess blocks")

        # First create the page with the initial batch of blocks (up to 100)
        print(f"Creating page with initial {len(initial_blocks)} blocks")

        new_page = notion.pages.create(
            parent={"database_id": "1818f3776f4f80158a6ac3fd054fc9c5"},  # Test Db
            properties=prop,
            children=initial_blocks,
        )
        page_id = new_page.get("id")
        print(f"Page created {page_id}")

        # If we have excess blocks, append them directly to the page
        if excess_blocks:
            print(f"Adding {len(excess_blocks)} excess blocks to append")

            # Append blocks in batches
            batch_size = MAX_BLOCKS_PER_PAGE

            for i in range(0, len(excess_blocks), batch_size):
                batch = excess_blocks[i:i+batch_size]
                batch_number = (i // batch_size) + 1
                print(
                    f"Appending batch {batch_number} with {len(batch)} blocks")

                try:
                    notion.blocks.children.append(
                        block_id=page_id,
                        children=batch
                    )
                    print(f"Successfully appended batch {batch_number}")
                except APIResponseError as append_error:
                    print(
                        f"Error appending batch {batch_number}: {append_error.code}")
                    logging.error(
                        f"Failed to append blocks batch {batch_number}: {append_error}")

except APIResponseError as error:
    if error.code == APIErrorCode.ObjectNotFound:
        logging.error(error)
    else:
        # Other error handling code
        logging.error(error.code)
        logging.error(error)
