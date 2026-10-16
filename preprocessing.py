import json
import math

import connect
import nltk


import pandas as pd
from nltk.stem import SnowballStemmer
import re
nltk.download('punkt_tab')


noticias_df = connect.fetch_data()
stemmer = SnowballStemmer('spanish')

def preprocess(text: str):

    # pasa el texto a minusculas y elimina simbolos
    text = text.lower()
    text = re.sub(r'([.,;:¡!¿?])', r' \1 ', text)

    # tokeniza
    tokens = nltk.word_tokenize(text, language='spanish')

    # se queda con las palabras que consisten en solamente simbolos
    words_raw = [token for token in tokens if token.isalpha()]

    # carga stopwords de la tabla stopwords
    conn = connect.connect_db()
    cursor = conn.cursor()
    cursor.execute("SELECT word FROM stopwords;")
    stopwords = { row[0].lower() for row in cursor.fetchall() }
    conn.close()

    # mantiene palabras que no son stopwords
    words = [word for word in words_raw if word not in stopwords]

    # stemming
    stemmed_words = [stemmer.stem(word) for word in words]

    return stemmed_words

def compute_bow(text: str):

    stemmed_words = preprocess(text)
    bow = {}

    # cuenta la cantidad de apariciones de cada stem por articulo
    for word in stemmed_words:
        if word in bow:
            bow[word] += 1
        else:
            bow[word] = 1

    return bow

def update_bow_in_db(dataframe):

    conn = connect.connect_db()
    cursor = conn.cursor()

    # para cada fila actualizar su bag of words con el computado y jsonificado
    for _, row in dataframe.iterrows():
        bow = compute_bow(row['contenido'])
        bow_json = json.dumps(bow, ensure_ascii=False)

        cursor.execute(
            "UPDATE noticias SET bag_of_words = %s WHERE id = %s;",
            (bow_json, int(row['id']))
        )

    conn.commit()
    cursor.close()
    conn.close()

def remove_bow_from_db(dataframe):

    conn = connect.connect_db()
    cursor = conn.cursor()

    # para cada fila actualizar su bag of words con un json vacio
    for index, row in dataframe.iterrows():
        cursor.execute(
            "UPDATE noticias SET bag_of_words = '{}'::jsonb WHERE id = %s;",
            (int(row['id']),)
        )

    conn.commit()
    cursor.close()
    conn.close()

def fetch_data_bow():

    # fetchear el bag of words de los primeros 10 articulos
    conn = connect.connect_db()
    df = pd.read_sql("SELECT bag_of_words FROM noticias LIMIT 10;", conn)
    conn.close()
    return df


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

    print(output)


if __name__ == '__main__':

    update_bow_in_db(noticias_df)
    noticias_bow_df = fetch_data_bow()
    print(noticias_bow_df)
    #remove_bow_from_db(noticias_df)
    #noticias_bow_df = fetch_data_bow()
    #print(noticias_bow_df)

    boolean_search_linear("economía AND inflación", noticias_bow_df)
    boolean_search_linear("banca OR finanzas", noticias_bow_df)
    boolean_search_linear("tecnología AND-NOT inflación", noticias_bow_df)

