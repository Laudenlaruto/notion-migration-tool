from notion_client.helpers import collect_paginated_api
import os
import logging
from notion_client import Client
from notion_client import APIErrorCode, APIResponseError

imagenum = 0
unsupported_blocks_removed = 0

MAX_CODE_BLOCK_LENGTH = 2000
MAX_BLOCKS_PER_PAGE = 100
MAX_NESTING_DEPTH = 2


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


def create_minimal_valid_block(block_type, structure_details=None):
    """
    Create a minimal valid block structure based on block type.
    Some block types require specific structures to be valid.

    Parameters:
    - block_type: The type of block to create
    - structure_details: Optional dictionary containing structure information (e.g., table width)
    """
    """
    Create a minimal valid block structure based on block type.
    Some block types require specific structures to be valid.
    """
    if block_type == "table":
        # Tables must have at least one table_row with the correct number of cells
        table_width = 1  # Default to 1 cell if structure_details not provided
        if structure_details and "table_width" in structure_details:
            table_width = structure_details["table_width"]

        # Create an empty table_row with the correct number of cells
        return {
            "object": "block",
            "type": "table_row",
            "table_row": {
                # Empty cell for each column
                "cells": [[] for _ in range(table_width)]
            }
        }
    elif block_type == "column_list":
        # Column lists should have at least one column
        return {
            "object": "block",
            "type": "column",
            "column": {
                "children": []
            }
        }
    elif block_type == "column":
        # Columns can have an empty children array
        return None
    else:
        # Default case - most blocks can have empty children arrays
        return None


def extract_deep_blocks(blocks, current_depth=0, parent_path=None):
    """
    Recursively identify blocks that exceed the maximum nesting depth.
    Returns a tuple containing:
    1. Modified blocks with deep blocks removed
    2. Dictionary of deep blocks with their parent paths
    """
    if not isinstance(blocks, list):
        return blocks, {}

    if parent_path is None:
        parent_path = []

    modified_blocks = []
    deep_blocks = {}

    logging.debug(
        f"Processing blocks at depth {current_depth}, path {parent_path}")

    # Enforce stricter nesting depth - extract blocks at the maximum depth
    # rather than exceeding it
    if current_depth >= MAX_NESTING_DEPTH:
        logging.warning(
            f"Found deeply nested blocks at depth {current_depth} - path {parent_path}")
        # Store entire block collection at too deep a level
        path_key = tuple(parent_path[:-1]) if parent_path else tuple()
        deep_blocks[path_key] = blocks
        return [], deep_blocks

    for i, block in enumerate(blocks):
        if not isinstance(block, dict):
            modified_blocks.append(block)
            continue

        # Create a path to this block
        current_path = parent_path + [i]

        # Check if this block has children
        if block.get("has_children"):
            block_type = block.get("type")

            # Print details about column_list blocks for debugging
            # Print details about special block types for debugging
            if block_type == "column_list":
                logging.debug(
                    f"Found column_list block at path {current_path}")
                if "column_list" in block and "children" in block["column_list"]:
                    logging.debug(
                        f"  Column list has {len(block['column_list']['children'])} columns")
                    for j, column in enumerate(block["column_list"]["children"]):
                        if column.get("type") == "column" and "column" in column:
                            has_children = "children" in column["column"]
                            children_count = len(column["column"].get(
                                "children", [])) if has_children else 0
                            logging.debug(
                                f"  Column {j} has_children: {has_children}, children_count: {children_count}")
            elif block_type == "table":
                logging.debug(
                    f"Found table block at path {current_path}")
                if "table" in block and "children" in block["table"]:
                    table_children = block["table"]["children"]
                    logging.debug(
                        f"  Table has {len(table_children)} rows")

                    # Determine the table width by analyzing the first row
                    table_width = 0
                    if table_children and len(table_children) > 0:
                        first_row = table_children[0]
                        if first_row.get("type") == "table_row" and "table_row" in first_row:
                            if "cells" in first_row["table_row"]:
                                table_width = len(
                                    first_row["table_row"]["cells"])
                                logging.debug(
                                    f"  Table width determined to be {table_width} cells")

                    for j, row in enumerate(table_children):
                        if row.get("type") == "table_row" and "table_row" in row:
                            has_cells = "cells" in row["table_row"]
                            cells_count = len(row["table_row"].get(
                                "cells", [])) if has_cells else 0
                            logging.debug(
                                f"  Row {j} has_cells: {has_cells}, cells_count: {cells_count}")
            if block_type and block_type in block and "children" in block[block_type]:
                children = block[block_type]["children"]

                # Log the structure for debugging
                logging.debug(
                    f"Block {current_path} of type {block_type} has {len(children)} children at depth {current_depth}")

                # If we're at max depth, extract children for later appending
                if current_depth >= MAX_NESTING_DEPTH - 1:
                    # Store these children with their parent path
                    path_key = tuple(current_path)
                    deep_blocks[path_key] = children

                    # List of block types that require children property to always be defined
                    # even if empty, or Notion API will reject the request
                    special_block_types = ["column_list", "column", "table"]

                    # Special handling for blocks that need to have a children property
                    # even if it's empty
                    if block_type in special_block_types:
                        logging.info(
                            f"Preserving minimal valid structure for {block_type} at path {current_path}")

                        # Create minimal valid structure
                        # Extract structure details if needed
                        structure_details = {}

                        if block_type == "table" and "children" in block[block_type] and len(block[block_type]["children"]) > 0:
                            # For tables, determine the width by looking at the first row
                            first_row = block[block_type]["children"][0]
                            if first_row.get("type") == "table_row" and "table_row" in first_row:
                                if "cells" in first_row["table_row"]:
                                    structure_details["table_width"] = len(
                                        first_row["table_row"]["cells"])
                                    logging.info(
                                        f"Detected table with {structure_details['table_width']} cells per row at {current_path}")

                        # Create minimal valid structure with the detected details
                        minimal_block = create_minimal_valid_block(
                            block_type, structure_details)

                        if minimal_block and block_type == "table":
                            # Tables must have at least one table_row child with the correct number of cells
                            block[block_type]["children"] = [minimal_block]
                            logging.info(
                                f"Added placeholder table_row to table at path {current_path} with {structure_details.get('table_width', 0)} cells")
                            # Column lists should have at least one column
                            block[block_type]["children"] = [minimal_block]
                            logging.info(
                                f"Added placeholder column to column_list at path {current_path}")
                        else:
                            # Other special blocks can have empty children arrays
                            block[block_type]["children"] = []
                    else:
                        # Remove children from the block for initial creation
                        del block[block_type]["children"]

                    logging.info(
                        f"Extracted {len(children)} deeply nested blocks at path {current_path} for later appending")
                else:
                    # Process children recursively
                    processed_children, child_deep_blocks = extract_deep_blocks(
                        children, current_depth + 1, current_path)
                    block[block_type]["children"] = processed_children
                    # Add any deep blocks found in children
                    deep_blocks.update(child_deep_blocks)

        modified_blocks.append(block)

    return modified_blocks, deep_blocks


def prepare_blocks_for_notion(blocks):
    """
    Prepare blocks for Notion API by handling validation constraints:
    - Splits code blocks exceeding MAX_CODE_BLOCK_LENGTH characters
    - Extracts blocks that exceed MAX_NESTING_DEPTH for later appending
    - Returns prepared blocks for initial page creation and data for later appending
    """
    if not isinstance(blocks, list):
        return blocks, [], {}

    # First split any code blocks that exceed the character limit
    blocks_with_split_code = split_long_code_blocks(blocks)

    # Extract deeply nested blocks
    blocks_with_proper_depth, deep_blocks = extract_deep_blocks(
        blocks_with_split_code)

    # Then separate blocks for initial page creation (up to MAX_BLOCKS_PER_PAGE)
    # from excess blocks that will be appended later
    initial_blocks = blocks_with_proper_depth[:MAX_BLOCKS_PER_PAGE]
    excess_blocks = blocks_with_proper_depth[MAX_BLOCKS_PER_PAGE:]

    return initial_blocks, excess_blocks, deep_blocks


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
    for doc in doctech:
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
                "Type": {"select": {"name": label}},
                "Owner": {"relation": [{"id": owner_id}]},
                "Experts": {"relation": [{"id": owner_id}]},
            }
        else:
            print("Original owner left the company")
            # Create page without owner
            prop = {
                "Name": {"title": [{"text": {"content": name}}]},
                "Type": {"select": {"name": label}},
            }

        total_blocks = len(all_blocks)
        print(f"Count of blocks: {total_blocks}")
        # Prepare blocks for Notion - handle code block length limits, block count limits, and nesting depth limits
        initial_blocks, excess_blocks, deep_blocks = prepare_blocks_for_notion(
            all_blocks)
        print(
            f"Prepared {len(initial_blocks)} initial blocks with {len(excess_blocks)} excess blocks and {len(deep_blocks)} deep block groups")
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

        # If we have deeply nested blocks, append them to their parent blocks
        if deep_blocks:
            print(f"Processing {len(deep_blocks)} deeply nested block groups")

            # First, get the full block structure of the created page to find block IDs
            try:
                # Get the block structure of the created page
                page_structure = collect_paginated_api(
                    notion.blocks.children.list, block_id=page_id)

                # Create a mapping of block positions to their IDs
                block_id_map = {}

                def map_block_ids(blocks, path_prefix=None):
                    if path_prefix is None:
                        path_prefix = []

                    for i, block in enumerate(blocks):
                        current_path = path_prefix + [i]
                        path_key = tuple(current_path)
                        block_id_map[path_key] = block.get("id")

                        # If this block has children, recursively map them
                        if block.get("has_children"):
                            child_blocks = collect_paginated_api(
                                notion.blocks.children.list, block_id=block.get("id"))
                            map_block_ids(child_blocks, current_path)

                # Map all block IDs in the created page
                map_block_ids(page_structure)

                # Now append deeply nested blocks to their parents
                for parent_path, children in deep_blocks.items():
                    if parent_path in block_id_map:
                        parent_id = block_id_map[parent_path]
                        print(
                            f"Appending deep blocks to parent at path {parent_path}")

                        try:
                            notion.blocks.children.append(
                                block_id=parent_id,
                                children=children
                            )
                            print(
                                f"Successfully appended deep blocks to parent {parent_id}")
                        except APIResponseError as deep_append_error:
                            print(
                                f"Error appending deep blocks: {deep_append_error.code}")
                            logging.error(
                                f"Failed to append deep blocks: {deep_append_error}")
                    else:
                        print(
                            f"Could not find block ID for parent path {parent_path}")

            except APIResponseError as structure_error:
                print(f"Error fetching page structure: {structure_error.code}")
                logging.error(
                    f"Failed to fetch page structure: {structure_error}")

except APIResponseError as error:
    if error.code == APIErrorCode.ObjectNotFound:
        logging.error(error)
    elif error.code == "validation_error":
        logging.error("validation_error")
        logging.error(error)

        # Extract information about deeply nested blocks from the error message
        error_msg = str(error)
        if "children" in error_msg and "should be not present" in error_msg:
            # Extract the problematic path from the error message
            import re
            path_match = re.search(
                r'body\.children\[\d+\]\..*?children', error_msg)
            if path_match:
                problematic_path = path_match.group(0)
                logging.error(
                    f"Detected problematic deeply nested path: {problematic_path}")

                # Count the nesting depth from the path
                nesting_depth = problematic_path.count("children")
                logging.error(
                    f"Nesting depth of problematic blocks: {nesting_depth}")
                logging.error(
                    f"Consider reducing MAX_NESTING_DEPTH to {MAX_NESTING_DEPTH-1}")

            # Log a warning that we need to extract more deeply nested blocks
            logging.error(
                "This error indicates blocks nested too deeply. Review MAX_NESTING_DEPTH setting.")
    else:
        # Other error handling code
        logging.error(error.code)
        logging.error(error)
