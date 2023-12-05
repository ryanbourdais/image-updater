import requests
import json
import sys
import base64
from ruamel.yaml import YAML
from pathlib import Path

#Configuration
api_org = "ryanbourdais-test-org" #The organization you'll be scanning.
api_key="" #Personal Access Token
new_branch_name="ImageUpdates" #Make sure this is unique.

#If any of the configuration elements up top are not filled in, ask for them here.
while api_org == "":
    api_org = input("Organization you wish to scan for repos:")

while api_key == "":
    api_key = input("Personal Access Token:")

while new_branch_name == "":
    new_branch_name = input("New branch name. Ensure it is unique:")

#Relevant but unlikely to change definitions here.
api_url = "https://api.github.com/"
headers =  {"Content-Type":"application/json", "Authorization":"Bearer " + api_key}

yaml = YAML()

deprecated_images=["ubuntu-2204:2023.08.1", "android:2023.11.1"]

#Get all repos.
def fetch_repos():
    call_url = api_url+"orgs/"+api_org+"/repos"
    res = requests.get(call_url, headers=headers)
    if res.status_code != 200:
        print("Organization " + api_org + " is not valid, or you don't have access to it. Confirm your API Key is correct as well.")
        sys.exit(1)
    return res

#For each repo, investigate for a .circleci/config.yml file being present in the main branch.
def repo_scan(repo):
    call_url = api_url + "repos/" + api_org + "/" + repo["name"] + "/contents/.circleci/config.yml"

    result = requests.get(call_url, headers=headers)


    if result.status_code == 200:
        data = json.loads(result.content)
        yaml_file = requests.get(data["download_url"], headers=headers)
        
        if str(yaml_file.content).find("machine:") == -1:
            print("No entry for \"machine:\" found")
            return False
        
        else:
            return_data = dict()
            return_data['content'] = str(yaml_file.content)
            return_data['sha'] = data["sha"]
            return return_data
    
    else:
        print("No .circleci/config.yml file found.")
        return False

#Determine if there are any machine entries at all.
def machine_check(config: str):
    if config.find("machine:") > -1:
        return True
    return False
    
#An initial call to get all repos.
response = fetch_repos()
repos = response.json()

#Run through each of them.
for r in repos:
    print("\n\n====== Working on Repo: " + r["name"] + " ======")
    result = repo_scan(r)
    if result == False: #Any error, we leave.
        continue

    result_text = result['content']

    #Split into different variables per newline, and remove leading dashes if they are present.
    result_text = result_text.replace(r"---", "")
    result_text = result_text.replace(r'\n', '\n')
    result_text = result_text[2:-1]

    result_yaml = yaml.load(result_text)
    change_made=False #If no changes are made, we can quit after this is done.

    print("\n=== Updating image tags ===")

    for attr, value in result_yaml['jobs'].items():
        if "machine" in value:

            ## The image name can be present under machines, or on the same depth as it, so we need to account for both.
            ## We first check for the same depth, then, we check under image.
            depth = 0
            if "image" in value:
                old_image = value["image"]
            elif "image" in value["machine"]:
                depth = 1
                old_image = value["machine"]["image"]
            else:
                print("Unexpected lack of image tag.")
                continue
            image = ""
            for i in deprecated_images:
                if i == old_image:
                    image_family = old_image.split(":")[0]
                    image_tag = input("\n deprecated image '" + old_image + "' found, specify new tag (if you would like default press enter):")
                    if image_tag == "":
                        image = image_family + ":default"
                    else:
                        image = image_family + image_tag
         
            #If the resource variable matches the Resource we started with, no change was made - otherwise, one was made.
            if (image != old_image and image != ""):
                change_made=True
                if depth == 0:
                    value["image"] = image
                elif depth == 1:
                    value["machine"]["image"] = image
                
                print("Updating from " + old_image + " to " + image)
        else:
            continue
    
    if change_made == False:
        print("No changes triggered, moving to next repo.")
        continue
    
    print("\n=== Writing file locally ===")
    #We save the file locally so that a copy is available to view, and so that it's easier to json-ify later.
    with open(r['name'] + ".yml", "w") as file:
        yaml.dump(result_yaml, file)
        print("Output for updated config saved in file: " + r['name'] + ".yml")

    #Prepare URLs for API Calls. Very repetitive, but hey.
    base_repo_url = api_url + "repos/" + api_org + "/" + r["name"]
    ref_head_url = base_repo_url + "/git/refs/heads"
    create_branch_url = base_repo_url + "/git/refs"
    update_url = base_repo_url + "/contents/.circleci/config.yml"
    create_pr_url = base_repo_url + "/pulls"

    #Attempt to create a branch. If it already exists, we will assume the script has already been run and move on.
    print("\n=== Creating Branch ===")
    branches = requests.get(ref_head_url, headers=headers).json()
    branch, sha = branches[-1]['ref'], branches[-1]['object']['sha']

    branch_create_res = requests.post(create_branch_url, headers=headers, data=json.dumps({
        "ref": "refs/heads/" + new_branch_name,
        "sha": sha
    }))
    if branch_create_res.status_code != 201 and branch_create_res.status_code != 200:
        print("Error when attempting to create branch: ", branch_create_res.status_code)
        print("Branch " + new_branch_name + " probably already exists, did you run the script before? If so, please delete the old branch.")
        continue

    print("Branch created.")
    print("\n=== Updating Config ===")

    #Open the file created above and encode it.
    file_content = Path(r['name']+".yml").read_text()
    file_content = file_content.replace('\n',"\n")
    file_content = str.encode(file_content)
    put_data = {
        "message": "Automatic image update for deprecated images.",
        "content": base64.b64encode(file_content).decode("utf-8"), 
        "sha": result['sha'],
        "branch": new_branch_name
    }

    put_result = requests.put(update_url, headers=headers, data=json.dumps(put_data))
    
    if put_result.status_code != 201 and put_result.status_code != 200:
        print("Error when attempting to update config.yml:", put_result.status_code)
        continue

    print("Config updated.") 
    
    #Successful commit to the branch, now we move on to a PR.
    print("\n=== Creating Pull Request ===")

    pr_data = {
        "title": "Update deprecated image tags",
        "head": new_branch_name,
        "base": r["default_branch"],
        "body": "This PR is opened by a script, designed to help bulk update deprecated image tags."
    }

    pr_result = requests.post(create_pr_url, headers=headers, data=json.dumps(pr_data))

    if pr_result.status_code != 201:
        print("Error creating pull request: ", pr_result.status_code)
        continue
    print("\n\n========================================")
    print("=========*      Success!      *=========")
    print("========================================")
    print("Pull request opened, URL: " + pr_result.json()["html_url"])