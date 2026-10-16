import json
import math

from preprocessing import *
import nltk
import pandas as pd
from nltk.stem import SnowballStemmer
import re

noticias_df = connect.fetch_data()
stemmer = SnowballStemmer('spanish')

def boolean_search_linear(query, dataframe):
    baselist = query.split(" ")
    if (len(baselist) % 2 == 0 and baselist[0] != "NOT") or (len(baselist) % 2 == 1 and baselist[0] == "NOT"):
        raise ValueError("Invalid amount of words in query - must be odd")
    negatefirst = False
    if baselist[0] == "NOT":
        negatefirst = True
    words = []
    operators = []
    nots = []
    toggle = True
    for i in range(math.ceil(len(baselist)/2)):
        e = baselist[i]
        if toggle:
            words.append(stemmer.stem(e))
        else:
            if e == "AND" or e == "OR":
                operators.append(e)
                nots.append(False)
            elif e == "AND-NOT":
                operators.append("AND")
                nots.append(True)
            elif e == "OR-NOT":
                operators.append("OR")
                nots.append(True)
            else:
                raise ValueError("Invalid operator")
        toggle = not toggle
    output = []
    for index, row in dataframe.iterrows():
        current_result = words[0] in row["bag_of_words"] if not negatefirst else words[0] not in row["bag_of_words"]
        for i in range(1, len(words)):
            curr = words[i] in row["bag_of_words"] if not nots[i] else words[i] not in row["bag_of_words"]
            if operators[i - 1] == "AND":
                current_result = curr and current_result
            elif operators[i - 1] == "OR":
                current_result = curr or current_result
            else:
                raise ValueError("Invalid operator")
        output.append(current_result)

"""
indice = {
 "word1": [("doc1", tf1), ("doc2", tf2), ("doc3", tf3)],
 "word2": [("doc2", tf2), ("doc4", tf4)],
 "word3": [("doc3", tf3), ("doc5", tf5)],
}
idf = {
 "word1": 3,
 "word2": 2,
 "word3": 2,
}
length = {
 "doc1": 15.5236,
 "doc2": 10.5236,
 "doc3": 5.5236,
}
"""
class InvertedIndex:
    def __init__(self):
        self.index = {}
        self.idf = {}
        self.length = {}
        self._docs = {}
    def build_from_db(self):
        # Leer desde PostgreSQL todos los bag of words
        # Construir el índice invertido, el idf y la norma (longitud) de cada documento
        self.index = {}
        self.idf = {}
        self.length = {}
        self._docs = {}
        conn = connect.connect_db()
        df = pd.read_sql("SELECT id, bag_of_words FROM noticias;", conn)
        conn.close()
        for index, row in df.iterrows():
            self._docs[f"doc{row["id"]}"] = row["bag_of_words"]
            for word, freq in row['bag_of_words'].items():
                if word not in self.index:
                    self.index[word] = []
                self.index[word].append((f"doc{row["id"]}", freq))
                if word not in self.idf:
                    self.idf[word] = 0
                self.idf[word] += 1
        for index, row in df.iterrows():
            sumofsquares = 0
            for word, freq in row['bag_of_words'].items():
                if self.idf.get(word, 0) == 0:
                    raise RuntimeError("IDF dictionary was not updated correctly")
                tf_idf = (1+math.log10(freq)) / self.idf[word]
                sumofsquares += tf_idf ** 2
            self.length[f"doc{row["id"]}"] = math.sqrt(sumofsquares)


    def L(self, word):
        return self.index.get(stemmer.stem(word), []) #modificado porque los datos ya están preprocesados
    def showDocument(self, doc_id, max_chars=80):
        return f"{doc_id}: " + str(self._docs.get(doc_id, ""))[:max_chars] + "..." #modificado porque botaba error al imprimir slice de diccionario
    def showDocuments(self, posting_list, max_chars=80):
        return [self.showDocument(doc_id, max_chars) for doc_id, *_ in posting_list]
    def cosine_search_linear(self, query, top_k=5):
        # Implementar la búsqueda por similitud de coseno lineal
        processed_query = {a: 0 for a in self.index.keys()}
        for word in query.split():
            stemmed_word = stemmer.stem(word)
            if stemmed_word in processed_query:
                processed_query[stemmed_word] = processed_query.get(stemmed_word, 0) + 1
            #si no está, se puede ignorar perfectamente para los documentos ya creados,
            #ya que su aporte con todos los otros vectores será 0 al no estar en ningún otro
        querylist = processed_query.items()
        querylist_tfidf_unnorm = [(a, 0 if b == 0 else (1+math.log10(b)) / (self.idf.get(a, 0) + 1) ) for (a, b) in querylist]
        sumofsquares = 0
        for (a, b) in querylist_tfidf_unnorm:
            sumofsquares += b ** 2
        querynorm = math.sqrt(sumofsquares)
        if querynorm == 0:
            return []
        query_tfidf = {word: weight / querynorm for (word, weight) in querylist_tfidf_unnorm}
        scores = []
        for doc_id, bow in self._docs.items():
            doc_tfidf = {}
            for word, freq in bow.items():
                if word in self.idf:
                    tf = 1 + math.log10(freq)
                    doc_tfidf[word] = tf / (self.idf[word] + 1)
            doc_norm = math.sqrt(sum(weight ** 2 for weight in doc_tfidf.values()))
            if doc_norm == 0:
                scores.append((doc_id, 0.0))
                continue
            score = sum(query_weight * (doc_tfidf.get(word, 0) / doc_norm) for word, query_weight in query_tfidf.items())
            scores.append((doc_id, score))
        return sorted(scores, key=lambda item: item[1], reverse=True)[:top_k]

    def cosine_search(self, query, top_k=5):
        score = {}
        # Implementar la búsqueda por similitud de coseno con índice invertido

        processed_query = {}
        for word in query.split():
            stemmed_word = stemmer.stem(word)
            if stemmed_word in self.index:
                processed_query[stemmed_word] = processed_query.get(stemmed_word, 0) + 1
        matching_docs = set()
        for word in processed_query.keys():
            for index, freq in self.L(word):
                matching_docs.add(index)
        querylist = processed_query.items()
        querylist_tfidf_unnorm = [(a, 0 if b == 0 else (1 + math.log10(b)) / (self.idf.get(a, 0) + 1)) for (a, b) in
                                  querylist]
        sumofsquares = 0
        for (a, b) in querylist_tfidf_unnorm:
            sumofsquares += b ** 2
        querynorm = math.sqrt(sumofsquares)
        if querynorm == 0:
            return []
        query_tfidf = {word: weight / querynorm for (word, weight) in querylist_tfidf_unnorm}

        score = []
        for doc_id in matching_docs:
            bow = self._docs[doc_id]
            doc_tfidf = {word: (1 + math.log10(freq)) / (self.idf[word] + 1) for word, freq in bow.items() if word in self.idf}
            doc_norm = math.sqrt(sum(weight ** 2 for weight in doc_tfidf.values()))
            if doc_norm == 0:
                score.append((doc_id, 0.0))
                continue
            score_indiv = sum(query_weight * doc_tfidf.get(word, 0) / doc_norm for word, query_weight in query_tfidf.items())
            score.append((doc_id, score_indiv))

        return sorted(score, key=lambda t: t[1], reverse=True)[:top_k]

def AND(list1, list2):
    docs2 = {index: freq for index, freq in list2}
    return [(index, freq+docs2.get(index, 0)) for index, freq in list1 if index in docs2]

def OR(list1, list2):
    output = dict(list1)
    for index, freq in list2:
        output[index] = output.get(index, 0) + freq
    return list(output.items())

def AND_NOT(list1, list2):
    docs2 = {index: freq for index, freq in list2}
    return [(index, freq) for index, freq in list1 if index not in docs2]





if __name__ == '__main__':

    idx = InvertedIndex()
    idx.build_from_db()
    # Pruebas (mínimo 5, combinando AND, OR, AND-NOT)
    print(idx.showDocuments(AND(idx.L("sostenibilidad"), AND(idx.L("ambiente"),
                                                             idx.L("renovables")))))
    print(idx.showDocuments(AND(idx.L("tecnología"), OR(idx.L("banca"), idx.L("finanzas")))))
    print(idx.showDocuments(AND_NOT(idx.L("economía"), idx.L("inflación"))))
    # Agregar 3 pruebas más
    print(idx.showDocuments(AND(idx.L("economía"), OR(idx.L("banca"), idx.L("finanzas")))))
    print(idx.showDocuments(AND_NOT(idx.L("sostenibilidad"), idx.L("economía"))))
    print(idx.showDocuments(AND_NOT(OR(idx.L("tecnología"), idx.L("innovación")), idx.L("inflación"))))

    queries = [
        "innovaciones en banca digital y tecnología financiera",
        "inflación y crecimiento económico",
        "sostenibilidad y energías renovables"
    ]
    for q in queries:
        print(f"\n{q}")
        print("Lineal :", idx.cosine_search_linear(q, 5))
        print("Índice :", idx.cosine_search(q, 5))