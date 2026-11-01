"""Offline CORS review plan. Never connects to R2 or changes a bucket."""
import argparse
import json
from copy import deepcopy
from urllib.parse import urlsplit

RULE_ID='astro-pdf-web-download-v1'


def download_rule(origins):
    allowed=[]
    for origin in origins:
        parsed=urlsplit(origin)
        if (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment or '*' in origin
                or any(c.isspace() for c in origin)):
            raise ValueError('Origem HTTPS explícita inválida; não inclua caminho, credenciais ou curingas.')
        if origin not in allowed:allowed.append(origin)
    if not allowed:raise ValueError('Informe ao menos uma origem web autorizada.')
    return {'ID':RULE_ID,'AllowedOrigins':allowed,'AllowedMethods':['GET'],
            'AllowedHeaders':['Range'],
            'ExposeHeaders':['Content-Disposition','Content-Length','Content-Range','ETag'],
            'MaxAgeSeconds':3600}


def plan_policy(current,origins):
    if set(current)!={'CORSRules'} or not isinstance(current['CORSRules'],list):
        raise ValueError('Política atual deve ser um objeto CORSRules exportado e revisado.')
    rules=deepcopy(current['CORSRules'])
    if any(not isinstance(rule,dict) for rule in rules):raise ValueError('Regra atual inválida.')
    if sum(rule.get('ID')==RULE_ID for rule in rules)>1:raise ValueError('ID de regra gerenciada duplicado.')
    proposed=[rule for rule in rules if rule.get('ID')!=RULE_ID]+[download_rule(origins)]
    warnings=[]
    if any('*' in origin for rule in rules if rule.get('ID')!=RULE_ID for origin in rule.get('AllowedOrigins',[])):
        warnings.append('A política atual contém origem com curinga; a revisão deve avaliar seu efeito global.')
    return {'applied':False,'current':current,'proposed':{'CORSRules':proposed},'warnings':warnings}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--current',required=True,help='Arquivo JSON local com a política atual, previamente exportada.')
    parser.add_argument('--origin',action='append',required=True)
    args=parser.parse_args()
    with open(args.current,encoding='utf-8') as source:current=json.load(source)
    print(json.dumps(plan_policy(current,args.origin),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
