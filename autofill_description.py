#!/usr/bin/env python3
import sys
import requests
import argparse
import json
import openai
import os
import tiktoken

SAMPLE_PROMPT = """
Write a pull request description focusing on the motivation behind the change and why it improves the project.
Go straight to the point.

The title of the pull request is "Enable valgrind on CI" and the following changes took place: 

Changes in file .github/workflows/build-ut-coverage.yml: @@ -24,6 +24,7 @@ jobs:
         run: |
           sudo apt-get update
           sudo apt-get install -y lcov
+          sudo apt-get install -y valgrind
           sudo apt-get install -y ${{ matrix.compiler.cc }}
           sudo apt-get install -y ${{ matrix.compiler.cxx }}
       - name: Checkout repository
@@ -48,3 +49,7 @@ jobs:
         with:
           files: coverage.info
           fail_ci_if_error: true
+      - name: Run valgrind
+        run: |
+          valgrind --tool=memcheck --leak-check=full --leak-resolution=med \
+            --track-origins=yes --vgdb=no --error-exitcode=1 ${build_dir}/test/command_parser_test
Changes in file test/CommandParserTest.cpp: @@ -566,7 +566,7 @@ TEST(CommandParserTest, ParsedCommandImpl_WhenArgumentIsSupportedNumericTypeWill
     unsigned long long expectedUnsignedLongLong { std::numeric_limits<unsigned long long>::max() };
     float expectedFloat { -164223.123f }; // std::to_string does not play well with floating point min()
     double expectedDouble { std::numeric_limits<double>::max() };
-    long double expectedLongDouble { std::numeric_limits<long double>::max() };
+    long double expectedLongDouble { 123455678912349.1245678912349L };
 
     auto command = UnparsedCommand::create(expectedCommand, "dummyDescription"s)
                        .withArgs<int, long, unsigned long, long long, unsigned long long, float, double, long double>();
"""

GOOD_SAMPLE_RESPONSE = """
## Overview
This pull request aims to integrate Valgrind into our Continuous Integration (CI) process to enhance memory leak detection and code reliability.

## Changes Made
    - **CI Workflow Update**: Included Valgrind installation and configured it to run with our test suite.
    - **Test Suite Adjustments**: Made minor adjustments to the test suite for better compatibility with Valgrind and improved test coverage.

## Impact
    - **Enhanced Code Quality**: Integrating Valgrind allows for automatic detection of memory leaks and access errors, leading to a more robust codebase.
    - **Improved Testing**: The adjustments in the test suite ensure comprehensive testing and accuracy.
"""


def main():
    parser = argparse.ArgumentParser(
        description="Use ChatGPT to generate a description for a pull request."
    )
    parser.add_argument(
        "--github-api-url", type=str, required=True, help="The GitHub API URL"
    )
    parser.add_argument(
        "--github-repository", type=str, required=True, help="The GitHub repository"
    )
    parser.add_argument(
        "--pull-request-id",
        type=int,
        required=True,
        help="The pull request ID",
    )
    parser.add_argument(
        "--github-token",
        type=str,
        required=True,
        help="The GitHub token",
    )
    parser.add_argument(
        "--openai-api-key",
        type=str,
        required=True,
        help="The OpenAI API key",
    )
    parser.add_argument(
        "--allowed-users",
        type=str,
        required=False,
        help="A comma-separated list of GitHub usernames that are allowed to trigger the action, empty or missing means all users are allowed",
    )
    args = parser.parse_args()

    github_api_url = args.github_api_url
    repo = args.github_repository
    github_token = args.github_token
    pull_request_id = args.pull_request_id
    openai_api_key = args.openai_api_key
    allowed_users = os.environ.get("INPUT_ALLOWED_USERS", "")
    if allowed_users:
        allowed_users = allowed_users.split(",")
    #open_ai_model
    open_ai_models = json.loads( os.environ.get("INPUT_OPENAI_MODELS") )
    #max_prompt_tokens = int(os.environ.get("INPUT_MAX_TOKENS", "1000"))
    max_response_tokens = int(os.environ.get("INPUT_MAX_RESPONSE_TOKENS"))
    model_temperature = float(os.environ.get("INPUT_TEMPERATURE"))
    model_sample_prompt = os.environ.get("INPUT_SAMPLE_PROMPT", SAMPLE_PROMPT)
    model_sample_response = os.environ.get(
        "INPUT_SAMPLE_RESPONSE", GOOD_SAMPLE_RESPONSE
    )
    file_types = os.environ.get("INPUT_FILE_TYPES", "").split(",")
    
    authorization_header = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": "token %s" % github_token,
    }
    
    status , completion_prompt = get_pull_request_description(allowed_users,github_api_url, repo, pull_request_id, authorization_header,file_types)
    if status != 0:
        return 1
    else:
        if completion_prompt == "":
            return status
    
    messages=[
            {
                "role": "system",
                "content": "You are a helpful assistant who writes pull request descriptions",
            },
            {"role": "user", "content": model_sample_prompt},
            {"role": "assistant", "content": model_sample_response},
            {"role": "user", "content": completion_prompt},
        ]
    # calculate for model selection
    model, prompt_token = model_selection(open_ai_models, messages, max_response_tokens)
    if model == "":
        print("No model available for this prompt")
        return 1

    token_left = open_ai_models[model] - prompt_token - max_response_tokens
    if token_left < 0:
        print(f"Model {model} does not have enough token to generate response")
        return 1

    extend_response_token = int( max_response_tokens + token_left * 0.8 )
    print(f"Using model {model} with {prompt_token} prompt tokens and reserve {extend_response_token} response token")
    
    openai.api_key = openai_api_key
    openai_response = openai.ChatCompletion.create(
        model=model,
        messages=messages,
        temperature=model_temperature,
        max_tokens=extend_response_token,
    )

    try:
        usage = openai_response.usage
        print(f"OpenAI API usage this request: {usage}")
    except:
        pass
    generated_pr_description = openai_response.choices[0].message.content
    redundant_prefix = "This pull request "
    if generated_pr_description.startswith(redundant_prefix):
        generated_pr_description = generated_pr_description[len(redundant_prefix) :]
        generated_pr_description = (
            generated_pr_description[0].upper() + generated_pr_description[1:]
        )
    print(f"Generated pull request description: '{generated_pr_description}'")
    issues_url = "%s/repos/%s/issues/%s" % (
        github_api_url,
        repo,
        pull_request_id,
    )
    update_pr_description_result = requests.patch(
        issues_url,
        headers=authorization_header,
        json={"body": generated_pr_description},
    )

    if update_pr_description_result.status_code != requests.codes.ok:
        print(
            "Request to update pull request description failed: "
            + str(update_pr_description_result.status_code)
        )
        print("Response: " + update_pr_description_result.text)
        return 1

def get_pull_request_description(allowed_users,github_api_url, repo, pull_request_id, authorization_header,file_types):
    pull_request_url = f"{github_api_url}/repos/{repo}/pulls/{pull_request_id}"
    pull_request_result = requests.get(
        pull_request_url,
        headers=authorization_header,
    )
    if pull_request_result.status_code != requests.codes.ok:
        print(
            "Request to get pull request data failed: "
            + str(pull_request_result.status_code)
        )
        return 1 , ""
    pull_request_data = json.loads(pull_request_result.text)

    if pull_request_data["body"]:
        print("Pull request already has a description, skipping")
        return 0 , ""

    if allowed_users:
        pr_author = pull_request_data["user"]["login"]
        if pr_author not in allowed_users:
            print(
                f"Pull request author {pr_author} is not allowed to trigger this action"
            )
            return 0 , ""

    pull_request_title = pull_request_data["title"]

    pull_request_files = []
    # Request a maximum of 30 pages (900 files)
    for page_num in range(1, 31):
        pull_files_url = f"{pull_request_url}/files?page={page_num}&per_page=30"
        pull_files_result = requests.get(
            pull_files_url,
            headers=authorization_header,
        )

        if pull_files_result.status_code != requests.codes.ok:
            print(
                "Request to get list of files failed with error code: "
                + str(pull_files_result.status_code)
            )
            return 1 , ""

        pull_files_chunk = json.loads(pull_files_result.text)

        if len(pull_files_chunk) == 0:
            break

        pull_request_files.extend(pull_files_chunk)

        completion_prompt = f"""
Write a pull request description focusing on the motivation behind the change and why it improves the project.
Go straight to the point.

The title of the pull request is "{pull_request_title}" and the following changes took place: \n
"""
    is_any_file_type_matched = False
    for pull_request_file in pull_request_files:
        # Not all PR file metadata entries may contain a patch section
        # For example, entries related to removed binary files may not contain it
        if "patch" not in pull_request_file:
            continue

        filename = pull_request_file["filename"]
        patch = pull_request_file["patch"]
        
        if not check_file_type(filename, file_types):
            print(f"skip file {filename}")
            continue
        
        is_any_file_type_matched = True
        completion_prompt += f"Changes in file {filename}: {patch}\n"
    
    if not is_any_file_type_matched:
        print("No file type matched")
        return 0, ""
    
    return 0, completion_prompt

def check_file_type(filename, file_types):
    for file_type in file_types:
        if filename.endswith(file_type):
            return True
    return False

def num_tokens_from_messages(messages, model="gpt-3.5-turbo-0613"):
    """Return the number of tokens used by a list of messages."""
    encoding = tiktoken.get_encoding("cl100k_base")
    tokens_per_message = 3
    tokens_per_name = 1
    num_tokens = 0
    for message in messages:
        num_tokens += tokens_per_message
        for key, value in message.items():
            num_tokens += len(encoding.encode(value))
            if key == "name":
                num_tokens += tokens_per_name
    num_tokens += 3
    return num_tokens

def model_selection( models , messages , max_response_tokens):
    candidate = []
    for model in models:
        max_token = models[model]
        max_prompt_tokens = max_token - max_response_tokens
        if max_prompt_tokens < 0:
            continue
        prompt_tokens = num_tokens_from_messages(messages, model)
        if prompt_tokens > max_prompt_tokens:
            continue
        print(f"May using model {model} with {prompt_tokens} prompt tokens and reserve {max_response_tokens} response token")
        candidate.append([model, models[model] , prompt_tokens])
    if len(candidate) == 0:
        return "",0
    # sort by max_token
    candidate.sort(key=lambda x: x[1])
    print(f"Using model {candidate[0][0]}")
    return candidate[0][0] , candidate[0][2]


if __name__ == "__main__":
    sys.exit(main())
