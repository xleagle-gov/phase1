import requests


def call_LocalContractFlow():
    url = "https://xleagle.app.n8n.cloud/webhook/9aba0243-ae36-4bfb-a7bc-1c6749ba8713"
    output= requests.get(url).json()
    print(output)
    return output


def call_samGovFlow():
    url = "https://xleagle.app.n8n.cloud/webhook/b3edc618-3048-431d-8df2-e05fdaadc8d8"
    output= requests.get(url).json()
    print(output)
    return output
    
call_samGovFlow()
