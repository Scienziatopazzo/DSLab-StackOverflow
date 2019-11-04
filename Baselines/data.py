from sqlalchemy import create_engine
import pandas as pd
import re

class Data:
    """note that all operations of this class should only return data in the time range set with 'set_time_range' """

    def __init__(self, db_address = None, verbose = 0, tables_with_time_res=None):

        if db_address is None:
            db_address = 'postgresql://localhost/crossvalidated'

        self.cnx = create_engine(db_address)

        self.macro_dict = dict(questionPostType=1, answerPostType=2)

        self.time_window_view_template = "InTimewindow_{}"

        self.verbose = verbose

        if tables_with_time_res is None:
            self.table_names_that_need_timerestrictions = ["Posts", "Users", "Votes"]
        else:
            self.table_names_that_need_timerestrictions = tables_with_time_res

        self.start, self.end = None, None
        self.drop_timewindow_views()
        self.create_date_indices()

    def set_time_range(self, start=None, end=None):
        self.start = start
        self.end = end

    def drop_timewindow_views(self):
        for raw_tablename in self.table_names_that_need_timerestrictions:
            viewname = self.time_window_view_template.format(raw_tablename)
            drop_command = "DROP VIEW IF EXISTS {};".format(viewname)
            self.execute_command(drop_command)

    def get_temp_tables_string(self, raw_tablenames = None):
        if raw_tablenames is None:
            raw_tablenames = self.table_names_that_need_timerestrictions
        elif len(raw_tablenames) == 0:
            return ""

        time_range_condition = self._time_range_condition_string(start=self.start, end=self.end)

        collector = list()
        for raw_tablename in raw_tablenames:
            viewname = self.time_window_view_template.format(raw_tablename)
            temp_table = " {} AS (SELECT * FROM {} {})".format(viewname, raw_tablename, time_range_condition)
            collector.append(temp_table)

        all_views = "WITH " + " ,".join(collector) + " "

        return all_views

    def get_users_with_tags(self):
        pass

    def get_tables(self):
        query = "SELECT * FROM pg_catalog.pg_tables WHERE schemaname='public';"
        return self.raw_query(query)

    def get_views(self):
        query = """select table_name from INFORMATION_SCHEMA.views WHERE table_schema = ANY (current_schemas(false))"""
        return self.raw_query(query)

    def execute_command(self, command):
        if self.verbose >= 1:
            print("Execute command >>" + command)
        res = self.cnx.execute(command)
        return res

    def log(self, string, level):
        if level <= self.verbose:
            print(string)

    def query(self, query_str, use_macros=False):
        """
        Run a custom query but Posts,Users,Votes tables will be restricted to contain rows created within the time intervall set in set_time_range

        :param query_str:
        :param use_macros: if True occurences of e.g. {questionPostType} will be replaced by the corresponding value in self.macro_dict
        :return:
        """
        if self.start is None and self.end is None:
            self.log("Taking all available data without timerestritionc!!", level=-1)

        if use_macros:
            query_str = query_str.format(**self.macro_dict)

        replaced_query = self.replace_all_tables_with_views(query_str)

        raw_tablenames_in_query = [raw_name for raw_name in self.table_names_that_need_timerestrictions
                                   if replaced_query.find(self.time_window_view_template.format(raw_name)) != -1]

        query_with_temp_tables = self.get_temp_tables_string(raw_tablenames_in_query) + replaced_query


        if self.verbose >= 1:
            print("Replaced Query>>>>"+ query_with_temp_tables)

        return self._raw_query(query_with_temp_tables)

    def raw_query(self, query_str):
        if self.verbose >= 1:
            print("WARING >> calling raw_query({}) means that data outside of the set timeperiod is also used".format(query_str))
        return self._raw_query(query_str)

    def _raw_query(self, query_str):
        return pd.read_sql_query(query_str, self.cnx)


    def replace_tablename_by_viewname(self, tablename, query):
        regex_str = r"([ \(,.=])" + tablename + r"(?=([. \n]|$))"
        replacement_str = r"\1" + self.time_window_view_template.format(tablename)

        new_query = re.sub(regex_str, replacement_str,  query, flags=re.IGNORECASE)
        return new_query

    def replace_all_tables_with_views(self, query):
        for tablename in self.table_names_that_need_timerestrictions:
            query = self.replace_tablename_by_viewname(tablename, query)
        return query

    def _time_range_condition_string(self, start=None, end=None, time_variable_name="CreationDate"):
        if start is None and end is None:
            # raise ValueError("in _time_range_condition_string at least one of start or end date has to be provided")
            return ""

        conds = list()
        if start:
            conds.append("{} >= date '{}'".format(time_variable_name, start))
        if end:
            conds.append("{} <= date '{}'".format(time_variable_name, end))

        return "WHERE " + (" AND ".join(conds))

    def create_date_indices(self, time_variable_name="CreationDate"):

        for tablename in self.table_names_that_need_timerestrictions:

            index_name = "idx_creationdate_{}".format(tablename)
            command = "CREATE INDEX IF NOT EXISTS {name} ON {table} using btree({varname})".format(name=index_name, table=tablename, varname=time_variable_name)

            self.execute_command(command)

    def get_questions(self):
        return self.query("SELECT Id, Title, Body, Tags FROM Posts WHERE PostTypeID = {}".format(self.questionPostType))

    def get_answered_questions(self, answer_upvotes_threshold):
        """get questions for which there is an answer"""
        # TODO use queries from get_accepted_answer and get_best_answer_above_threshold
        # Where Id in (accepted Naswer query) or Id in (best answer query)
        pass




    def get_answer_for_question(self, threshold=None):
        """
        :return: dataframe question_id (index), answerer_id, answer_post_id, score || all pairs where user anwerer_id answered question question_id to an acceptable standard.
        there is at most one answerer per question it is the accepted answer if it exists or the answer with the hightest score above the given threshold.
        Note that the index of the returned dataframe corresponds to the question_id
        """
        accepted_answers = self.get_accepted_answer()
        if threshold:
            best_answers = self.get_best_answer_above_threshold(threshold)

            filtered_best_answers = best_answers.drop(index=accepted_answers.index, errors="ignore") # forget all best answers for questions where an accepted answer exists

            result = pd.concat([accepted_answers, filtered_best_answers], verify_integrity=True)
            return result
        else:
            return accepted_answers

    def get_best_answer_above_threshold(self, upvotes_threshold):
        """
        Returns everything where the answer is in the set timeperiod, the question might have been asked earlier.
        :return: dataframe question_id, answerer_id, answer_post_id with the answer with the most number of upvotes as long as it is above the given threshold. Doesn't care whether answer is accepted or not
        """
        # this query results in an endless loop I don't know why:
        #
        # q = """SELECT Q.Id as question_id, A.OwnerUserId as answerer_id, A.Id AS answer_post_id
        #     FROM (SELECT Id FROM Posts WHERE PostTypeId = {{questionPostType}} AND AcceptedAnswerId IS NULL) AS Q
        #         INNER JOIN
        #     (SELECT Id, OwnerUserId, Score, ParentId FROM Posts WHERE PostTypeId = {{answerPostType}} AND Score >= {threshold} AND OwnerUserId IS NOT NULL) AS A
        #     ON A.ParentId = Q.Id
        #     ORDER BY Q.Id, A.Score DESC;""".format(threshold=upvotes_threshold)

        q2 = """SELECT Distinct ON (ParentId) ParentId as question_id, OwnerUserId as answerer_id,  Id as answer_post_id, Score FROM Posts WHERE PostTypeId = {{answerPostType}} AND Score >= {threshold} AND OwnerUserId IS NOT NULL AND ParentId in (select Id from Posts WHERE PostTypeId = {{questionPostType}}) ORDER BY ParentId, Score Desc""".format(threshold=upvotes_threshold)

        out = self.query(q2, use_macros=True)

        out.set_index("question_id", inplace=True, verify_integrity=True)
        return out

    def get_accepted_answer(self):
        """

        :return: dataframe question_id, answerer_id, answer_post_id with the accepted answer for each question
        """

        q = """SELECT Q.Id as question_id, A.OwnerUserId as answerer_id, A.Id AS answer_post_id, A.Score FROM Posts Q INNER JOIN Posts A on Q.AcceptedAnswerId = A.Id WHERE Q.PostTypeId = {questionPostType} AND A.PostTypeId = {answerPostType} AND A.OwnerUserId IS NOT NULL"""

        out = self.query(q, use_macros=True)
        out.set_index("question_id", inplace=True, verify_integrity=True)
        return out

    def get_user_tags(self):
        q = """SELECT OwnerUserId as User_Id, string_agg(Tags, '') as question_tags FROM Posts WHERE PostTypeId = {questionPostType} Group By OwnerUserId
            """

        user_question_tags = self.query(q, use_macros=True)

        q_answers = """SELECT A.OwnerUserId as User_id, string_agg(Q.Tags, '') as answered_tags FROM Posts Q INNER JOIN Posts A on A.ParentId = Q.Id WHERE A.PostTypeId = {answerPostType} GROUP BY A.OwnerUserId"""

        user_answers_tags = self.query(q_answers, use_macros=True)

        both = pd.merge(user_question_tags, user_answers_tags, on="user_id", how="outer")

        both = both.fillna("")

        both["user_tags"] = both.question_tags + both.answered_tags

        both.user_id = pd.to_numeric(both.user_id)

        return both


class GetAnswerersStrategy:
    # an instance to get answerers to questions

    # answerer users for ids (sets)
    def __init__(self, votes_threshold=None, db_access = None, verbose=0):
        self.votes_threshold = votes_threshold
        if db_access is None:
            self.db_access = Data(verbose=verbose)
        else:
            self.db_access = db_access

    def get_answerers_set(self, question_ids, before_timepoint=None):
        # all users that answered any of the question_ids
        pass

    def get_answers_list(self, question_ids, before_timepoint=None):
        if self.votes_threshold is None:
            #we only take accepted answers
            additional_cond = ""
        else:
            additional_cond = " OR A.Score >= {}".format(self.votes_threshold)

        self.db_access.set_time_range(start=None, end=before_timepoint)


        q  = """
        SELECT Q.Id as question_id, A.Id as answer_id, A.OwnerUserId as answerer_user_id
        FROM Posts A INNER JOIN Posts Q on A.ParentId = Q.Id
        WHERE A.ParentId IN {question_id_list}  AND (Q.AcceptedAnswerId = A.Id {additional_cond})
        """.format(question_id_list = sql_formatl_list(question_ids), additional_cond=additional_cond)

        result = self.db_access.query(q)
        return result


def sql_formatl_list(l):
    return "({})".format(", ".join([str(int(i)) for i in l]))
