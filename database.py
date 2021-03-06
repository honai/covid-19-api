import json
import logging
from datetime import datetime
from typing import List, Dict, Union

from pymongo import MongoClient, DESCENDING
from elasticsearch import Elasticsearch

from util import load_config
from constants import (
    ITOPICS,
    ITOPIC_ETOPIC_MAP,
    ETOPIC_ITOPICS_MAP,
    ECOUNTRY_ICOUNTRIES_MAP,
    ETOPIC_TRANS_MAP,
    ECOUNTRY_TRANS_MAP,
    SCORE_THRESHOLD,
    RUMOR_THRESHOLD,
    USEFUL_THRESHOLD
)


class DBHandler:

    def __init__(self, host: str, port: int, db_name: str, collection_name: str, es_host: str, es_port: int):
        self.client = MongoClient(host, port)
        self.db = self.client[db_name]
        self.collection = self.db.get_collection(name=collection_name)

        self.es = Elasticsearch(f'{es_host}:{es_port}')

    def upsert_page(self, document: dict) -> None:
        """Add a page to the database. If the page has already been registered, update the page."""

        def reshape_snippets(snippets: Dict[str, List[str]]) -> Dict[str, str]:
            # Find a general snippet.
            general_snippet = ''
            for itopic in ITOPICS:
                if itopic in snippets:
                    general_snippet = snippets[itopic][0] if snippets[itopic] else ''
                    break

            # Reshape snippets.
            reshaped = {}
            for itopic in ITOPICS:
                snippets_about_topic = snippets.get(itopic, [])
                if snippets_about_topic and snippets_about_topic[0]:
                    reshaped[itopic] = snippets_about_topic[0].strip()
                elif general_snippet:
                    reshaped[itopic] = general_snippet
                else:
                    reshaped[itopic] = ''
            return reshaped

        is_about_covid_19: int = document['classes']['is_about_COVID-19']
        country: str = document['country']
        if not document['orig']['title']:
            return
        orig = {
            'title': document['orig']['title'].strip(),  # type: str
            'timestamp': document['orig']['timestamp'],  # type: str
            'simple_timestamp': datetime.fromisoformat(document['orig']['timestamp']).date().isoformat(),  # type: str
        }
        if not document['ja_translated']['title']:
            return
        ja_translated = {
            'title': document['ja_translated']['title'].strip(),  # type: str
            'timestamp': document['ja_translated']['timestamp'],  # type: str
        }
        if not document['en_translated']['title']:
            return
        en_translated = {
            'title': document['en_translated']['title'].strip(),  # type: str
            'timestamp': document['en_translated']['timestamp'],  # type: str
        }
        url: str = document['url']
        topics_to_score = {
            key: value for key, value in document['classes_bert'].items() if key in ITOPICS and value > 0.5
        }
        topics: Dict[str, float] = dict()
        for idx, (topic, score) in enumerate(sorted(topics_to_score.items(), key=lambda x: x[1], reverse=True)):
            if idx == 0 or score > SCORE_THRESHOLD:
                topics[topic] = float(score)
            else:
                break
        ja_snippets = reshape_snippets(document['snippets'])
        en_snippets = reshape_snippets(document['snippets_en'])

        is_checked = 0
        is_useful = 1 if document['classes_bert']['is_useful'] > USEFUL_THRESHOLD else 0
        is_clear = document['classes']['is_clear']
        is_about_false_rumor = 1 if document['classes_bert']['is_about_false_rumor'] > RUMOR_THRESHOLD else 0

        domain = document.get('domain', '')
        ja_domain_label = document.get('domain_label', '')
        en_domain_label = document.get('domain_label_en', '')
        document_ = {
            'country': country,
            'displayed_country': country,
            'orig': orig,
            'ja_translated': ja_translated,
            'en_translated': en_translated,
            'url': url,
            'topics': topics,
            'ja_snippets': ja_snippets,
            'en_snippets': en_snippets,
            'is_checked': is_checked,
            'is_about_COVID-19': is_about_covid_19,
            'is_useful': is_useful,
            'is_clear': is_clear,
            'is_about_false_rumor': is_about_false_rumor,
            'domain': domain,
            'ja_domain_label': ja_domain_label,
            'en_domain_label': en_domain_label
        }

        existing_page = self.collection.find_one({'page.url': url})
        if existing_page and orig['timestamp'] > existing_page['page']['orig']['timestamp']:
            self.collection.update_one(
                {'page.url': url},
                {'$set': {'page': document_}},
                upsert=True
            )
        elif not existing_page:
            self.collection.insert_one({'page': document_})

    def classes(self, etopic: str, ecountry: str, start: int, limit: int, lang: str, query: str):
        if etopic == 'search':
            return self.search(ecountry, start, limit, lang, query)

        etopic = ETOPIC_TRANS_MAP.get((etopic, 'ja'), etopic)
        ecountry = ECOUNTRY_TRANS_MAP.get((ecountry, 'ja'), ecountry)

        if etopic and ecountry:
            itopics = ETOPIC_ITOPICS_MAP.get(etopic, [])
            icountries = ECOUNTRY_ICOUNTRIES_MAP.get(ecountry, [])
            reshaped_pages = self.get_pages(itopics, icountries, start, limit, lang)
        elif etopic:
            itopics = ETOPIC_ITOPICS_MAP.get(etopic, [])
            reshaped_pages = {}
            for ecountry, icountries in ECOUNTRY_ICOUNTRIES_MAP.items():
                if ecountry == 'all':
                    continue
                reshaped_pages[ecountry] = self.get_pages(itopics, icountries, start, limit, lang)
        else:
            reshaped_pages = {}
            for etopic, itopics in ETOPIC_ITOPICS_MAP.items():
                if etopic == 'all':
                    continue
                reshaped_pages[etopic] = {}
                for ecountry, icountries in ECOUNTRY_ICOUNTRIES_MAP.items():
                    if ecountry == 'all':
                        continue
                    reshaped_pages[etopic][ecountry] = self.get_pages(itopics, icountries, start, limit, lang)
        return reshaped_pages

    def countries(self, ecountry: str, etopic: str, start: int, limit: int, lang: str):
        etopic = ETOPIC_TRANS_MAP.get((etopic, 'ja'), etopic)
        ecountry = ECOUNTRY_TRANS_MAP.get((ecountry, 'ja'), ecountry)

        if ecountry and etopic:
            itopics = ETOPIC_ITOPICS_MAP.get(etopic, [])
            icountries = ECOUNTRY_ICOUNTRIES_MAP.get(ecountry, [])
            reshaped_pages = self.get_pages(itopics, icountries, start, limit, lang)
        elif ecountry:
            icountries = ECOUNTRY_ICOUNTRIES_MAP.get(ecountry, [])
            reshaped_pages = {}
            for etopic, itopics in ETOPIC_ITOPICS_MAP.items():
                if etopic == 'all':
                    continue
                reshaped_pages[etopic] = self.get_pages(itopics, icountries, start, limit, lang)
        else:
            reshaped_pages = {}
            for ecountry, icountries in ECOUNTRY_ICOUNTRIES_MAP.items():
                if ecountry == 'all':
                    continue
                reshaped_pages[ecountry] = {}
                for etopic, itopics in ETOPIC_ITOPICS_MAP.items():
                    if etopic == 'all':
                        continue
                    reshaped_pages[ecountry][etopic] = self.get_pages(itopics, icountries, start, limit, lang)
        return reshaped_pages

    def search(self, ecountry: str, start: int, limit: int, lang: str, query: str):
        def get_es_query(regions: List[str]):
            return {
                'query': {
                    'bool': {
                        'must': [
                            {'bool': {'should': [{'term': {'region': region}} for region in regions]}},
                            {'match': {'text': query}},
                        ],
                    }
                },
                'sort': [{'timestamp.local': {'order': 'desc', 'nested': {'path': 'timestamp'}}}],
                'from': start,
                'size': limit,
            }

        def convert_hits_to_pages(hits: list) -> list:
            reshaped_pages = []
            for hit in hits:
                doc = self.collection.find_one(filter={'page.url': hit['_source']['url']})
                if doc:
                    reshaped_pages.append(self.reshape_page(doc['page'], lang))
            return reshaped_pages

        if ecountry:
            body = get_es_query([c for c in ECOUNTRY_ICOUNTRIES_MAP.get(ecountry, [])])
            r = self.es.search(index='covid19-pages-ja', body=body)
            return convert_hits_to_pages(r['hits']['hits'])
        else:
            reshaped_pages = {}
            for ecountry, icountries in ECOUNTRY_ICOUNTRIES_MAP.items():
                if ecountry == 'all':
                    continue
                body = get_es_query(icountries)
                r = self.es.search(index='covid19-pages-ja', body=body)
                reshaped_pages[ecountry] = convert_hits_to_pages(r['hits']['hits'])
            return reshaped_pages

    def get_pages(self, itopics: List[str], icountries: List[str], start: int, limit: int, lang: str) -> List[dict]:
        filter_ = self.get_filter(itopics, icountries)
        sort_ = self.get_sort(itopics)
        cur = self.collection.find(filter=filter_, sort=sort_)
        return [self.reshape_page(doc['page'], lang) for doc in cur.skip(start).limit(limit)]

    @staticmethod
    def get_filter(itopics: List[str] = None, icountries: List[str] = None) -> Dict[str, List]:
        filters = [{'page.is_about_COVID-19': 1}]
        if itopics:
            filters += [{'$or': [{f'page.topics.{itopic}': {'$exists': True}} for itopic in itopics]}]
        if icountries:
            filters += [{'page.displayed_country': {'$in': icountries}}]
        return {'$and': filters}

    @staticmethod
    def get_sort(itopics: List[str] = None):
        sort_ = [('page.orig.simple_timestamp', DESCENDING)]
        if itopics:
            sort_ += [(f'page.topics.{itopic}', DESCENDING) for itopic in itopics]
        return sort_

    @staticmethod
    def reshape_page(page: dict, lang: str) -> dict:
        page['topics'] = [
            {
                'name': ETOPIC_TRANS_MAP[(ITOPIC_ETOPIC_MAP[itopic], lang)],
                'snippet': page[f'{lang}_snippets'][itopic],
                'relatedness': page['topics'][itopic]
            }
            for itopic in page['topics']
        ]
        page['translated'] = page[f'{lang}_translated']
        page['domain_label'] = page[f'{lang}_domain_label']
        page['is_about_false_rumor'] = 1 if page['domain'] == 'fij.info' else page['is_about_false_rumor']
        del page['ja_snippets']
        del page['en_snippets']
        del page['ja_translated']
        del page['en_translated']
        del page['ja_domain_label']
        del page['en_domain_label']
        return page

    def update_page(self,
                    url: str,
                    is_about_covid_19: bool,
                    is_useful: bool,
                    is_about_false_rumor: bool,
                    icountry: str,
                    etopics: List[str],
                    notes: str,
                    category_check_log_path: str) -> Dict[str, Union[int, str, List[str]]]:
        new_is_about_covid_19 = 1 if is_about_covid_19 else 0
        new_is_useful = 1 if is_useful else 0
        new_is_about_false_rumor = 1 if is_about_false_rumor else 0
        new_etopics = {ETOPIC_ITOPICS_MAP[etopic][0]: 1.0 for etopic in etopics}

        self.collection.update_one(
            {'page.url': url},
            {'$set': {
                'page.is_about_COVID-19': new_is_about_covid_19,
                'page.is_useful': new_is_useful,
                'page.is_about_false_rumor': new_is_about_false_rumor,
                'page.is_checked': 1,
                'page.displayed_country': icountry,
                'page.topics': new_etopics
            }},
            upsert=True
        )
        updated = {
            'url': url,
            'is_about_COVID-19': new_is_about_covid_19,
            'is_useful': new_is_useful,
            'is_about_false_rumor': new_is_about_false_rumor,
            'new_country': icountry,
            'new_topics': list(new_etopics.keys()),
            'notes': notes,
            'time': datetime.now().isoformat()
        }
        with open(category_check_log_path, mode='a') as f:
            json.dump(updated, f, ensure_ascii=False)
            f.write('\n')
        return updated


def main():
    cfg = load_config()

    logger = logging.getLogger(__file__)
    logger.setLevel(20)
    fh = logging.FileHandler(cfg['database']['log_path'], mode='a')
    logger.addHandler(fh)
    formatter = logging.Formatter('%(asctime)s:%(lineno)d:%(levelname)s:%(message)s')
    fh.setFormatter(formatter)

    mongo = DBHandler(
        host=cfg['database']['host'],
        port=cfg['database']['port'],
        db_name=cfg['database']['db_name'],
        collection_name=cfg['database']['collection_name'],
        es_host=cfg['es']['host'],
        es_port=cfg['es']['port'],
    )

    # add pages to the database or update pages
    with open(cfg['database']['input_page_path'], mode='r', encoding='utf-8') as f:
        for line in f:
            mongo.upsert_page(json.loads(line.strip()))
    num_docs = sum(1 for _ in mongo.collection.find())
    logger.log(20, f'Number of pages: {num_docs}')

    # add category-checked pages
    with open(cfg['database']['category_check_log_path'], mode='r') as f:
        for line in f:
            if not line.strip():
                continue
            category_checked_page = json.loads(line.strip())
            existing_page = mongo.collection.find_one({'page.url': category_checked_page['url']})
            if not existing_page:
                continue

            mongo.collection.update_one(
                {'page.url': category_checked_page['url']},
                {'$set': {
                    'page.is_about_COVID-19': category_checked_page['is_about_COVID-19'],
                    'page.is_useful': category_checked_page['is_useful'],
                    'page.is_about_false_rumor': category_checked_page.get('is_about_false_rumor', 0),
                    'page.is_checked': 1,
                    'page.displayed_country': category_checked_page['new_country'],
                    'page.topics': {new_topic: 1.0 for new_topic in category_checked_page['new_topics']}
                }},
            )


if __name__ == '__main__':
    main()
